"""The person's private tracker database. It stores no email bodies. Terms are defined in GLOSSARY.md."""
import sqlite3
from contextlib import contextmanager

from job_tracker.gmail import DATA_DIR
from job_tracker.rules import STATUS_FOR_CATEGORY, decide

DB_FILE = DATA_DIR / "tracker.db"
STATUSES = ("Applied", "Interviewing", "Rejected", "Offer")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    company TEXT NOT NULL,
    role TEXT,
    status TEXT NOT NULL,
    needs_reply_since TEXT,
    needs_reply_email TEXT,
    removed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    sender TEXT NOT NULL,
    date TEXT NOT NULL,
    link TEXT NOT NULL,
    category TEXT NOT NULL,
    company TEXT,
    role TEXT,
    state TEXT NOT NULL CHECK (state IN ('attached', 'review', 'discarded')),
    review_reason TEXT,
    application_id INTEGER REFERENCES applications (id)
);
CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications (id),
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    at TEXT NOT NULL,
    who TEXT NOT NULL CHECK (who IN ('tracker', 'person')),
    email_id TEXT REFERENCES emails (id)
);
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'crashed')),
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    finished_at TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    total INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS emails_by_application ON emails (application_id);
CREATE INDEX IF NOT EXISTS emails_by_state ON emails (state);
CREATE INDEX IF NOT EXISTS changes_by_application ON change_log (application_id);
"""

_APPLICATION = """
SELECT a.*, reply.link AS needs_reply_link,
       COALESCE((SELECT MAX(date) FROM emails WHERE application_id = a.id), a.created_at) AS last_email
FROM applications a LEFT JOIN emails reply ON reply.id = a.needs_reply_email
"""


def connect(path=DB_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")  # lets the page read while a sync is writing
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def is_processed(conn, email_id):
    return conn.execute("SELECT 1 FROM emails WHERE id = ?", (email_id,)).fetchone() is not None


def save_email(conn, email, result, link, now):
    """Apply one classified email in one transaction, so it only counts as processed if everything saved.

    A result of None means the model's answer was unusable, so the person decides instead.
    """
    with transaction(conn):
        if is_processed(conn, email["id"]):
            return
        if result is None:
            result = {"category": "unclear", "company": None, "role": None}
            decision = {"action": "review", "reason": "The model gave an unusable answer for this email."}
        else:
            decision = decide(result["category"], result["company"], result["role"], _matchable(conn))
        action = decision["action"]
        state = {"discard": "discarded", "review": "review"}.get(action, "attached")
        conn.execute(
            "INSERT INTO emails (id, subject, sender, date, link, category, company, role, state, review_reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                email["id"], email["subject"], email["sender"], email["date"], link,
                result["category"], result["company"], result["role"], state, decision.get("reason"),
            ),
        )
        if state != "attached":
            return
        if action == "create":
            _create(conn, decision["company"], decision["role"], "Applied", "tracker", email["id"], now)
            return
        application_id = decision["application_id"]
        _link(conn, email["id"], application_id)
        if decision["role"]:
            _change(conn, application_id, "role", decision["role"], "tracker", email["id"], now)
        if action == "log":
            _log(conn, application_id, "repeat", None, result["category"], "tracker", email["id"], now)
        elif action == "update":
            if decision["status"]:
                _change(conn, application_id, "status", decision["status"], "tracker", email["id"], now)
            if decision["needs_reply"]:
                _set_needs_reply(conn, application_id, email["date"], email["id"], "tracker", now)


def _matchable(conn):
    return conn.execute("SELECT id, company, role, status FROM applications WHERE removed = 0").fetchall()


def _create(conn, company, role, status, who, email_id, now):
    application_id = conn.execute(
        "INSERT INTO applications (company, role, status, created_at) VALUES (?, ?, ?, ?)",
        (company, role, status, now),
    ).lastrowid
    _link(conn, email_id, application_id)
    _log(conn, application_id, "created", None, status, who, email_id, now)
    return application_id


def _link(conn, email_id, application_id):
    conn.execute(
        "UPDATE emails SET application_id = ?, state = 'attached', review_reason = NULL WHERE id = ?",
        (application_id, email_id),
    )


def _log(conn, application_id, field, old, new, who, email_id, now):
    conn.execute(
        "INSERT INTO change_log (application_id, field, old_value, new_value, at, who, email_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (application_id, field, old, new, now, who, email_id),
    )


def _change(conn, application_id, field, new, who, email_id, now):
    """Set one of the fields below and log it, doing nothing if it already has that value."""
    column = {"company": "company", "role": "role", "status": "status"}[field]  # never build SQL from outside input
    old = conn.execute(f"SELECT {column} FROM applications WHERE id = ?", (application_id,)).fetchone()[0]
    if old != new:
        conn.execute(f"UPDATE applications SET {column} = ? WHERE id = ?", (new, application_id))
        _log(conn, application_id, field, old, new, who, email_id, now)


def _set_needs_reply(conn, application_id, since, email_id, who, now):
    """Turn the marker on, or off when since is None. A marker already on keeps its original wait."""
    was_on = conn.execute(
        "SELECT needs_reply_since FROM applications WHERE id = ?", (application_id,)
    ).fetchone()[0] is not None
    if was_on == (since is not None):
        return
    conn.execute(
        "UPDATE applications SET needs_reply_since = ?, needs_reply_email = ? WHERE id = ?",
        (since, email_id, application_id),
    )
    _log(conn, application_id, "needs_reply", "yes" if was_on else "no", "no" if was_on else "yes", who, email_id, now)


# What the person does on the page. Each action only touches emails still in the expected place,
# so a double click or a stale page changes nothing.

def attach_email(conn, email_id, application_id, now):
    """Link an email from the Review List to an Application and apply what the email says."""
    with transaction(conn):
        email = _email_in(conn, email_id, "review")
        if email is None or application(conn, application_id) is None:
            return
        _link(conn, email_id, application_id)
        if email["role"] and not application(conn, application_id)["role"]:
            _change(conn, application_id, "role", email["role"], "person", email_id, now)
        if email["category"] in STATUS_FOR_CATEGORY:
            _change(conn, application_id, "status", STATUS_FOR_CATEGORY[email["category"]], "person", email_id, now)
        elif email["category"] == "info_request":
            _set_needs_reply(conn, application_id, email["date"], email_id, "person", now)
        else:
            _log(conn, application_id, "attached", None, email["category"], "person", email_id, now)


def create_from_email(conn, email_id, company, role, now):
    """Create an Application from an email on the Review List, returning its id."""
    with transaction(conn):
        email = _email_in(conn, email_id, "review")
        if email is None:
            return None
        status = STATUS_FOR_CATEGORY.get(email["category"], "Applied")
        application_id = _create(conn, company, role, status, "person", email_id, now)
        if email["category"] == "info_request":
            _set_needs_reply(conn, application_id, email["date"], email_id, "person", now)
        return application_id


def dismiss_email(conn, email_id):
    conn.execute(
        "UPDATE emails SET state = 'discarded', review_reason = NULL WHERE id = ? AND state = 'review'", (email_id,)
    )


def send_to_review(conn, email_id):
    conn.execute(
        "UPDATE emails SET state = 'review', review_reason = 'You sent this to the Review List.'"
        " WHERE id = ? AND state = 'discarded'",
        (email_id,),
    )


def edit_application(conn, application_id, company, role, status, needs_reply, now):
    with transaction(conn):
        if application(conn, application_id) is None:
            return
        _change(conn, application_id, "company", company, "person", None, now)
        _change(conn, application_id, "role", role, "person", None, now)
        _change(conn, application_id, "status", status, "person", None, now)
        _set_needs_reply(conn, application_id, now if needs_reply else None, None, "person", now)


def mark_reply_done(conn, application_id, now):
    with transaction(conn):
        if application(conn, application_id) is not None:
            _set_needs_reply(conn, application_id, None, None, "person", now)


def set_removed(conn, application_id, removed, now):
    with transaction(conn):
        current = application(conn, application_id)
        if current is not None and bool(current["removed"]) != removed:
            conn.execute("UPDATE applications SET removed = ? WHERE id = ?", (int(removed), application_id))
            _log(conn, application_id, "removed", "yes" if current["removed"] else "no", "yes" if removed else "no", "person", None, now)


def _email_in(conn, email_id, state):
    return conn.execute("SELECT * FROM emails WHERE id = ? AND state = ?", (email_id, state)).fetchone()


# Reads for the page.

def application(conn, application_id):
    return conn.execute(f"{_APPLICATION} WHERE a.id = ?", (application_id,)).fetchone()


def applications(conn, removed=False):
    return conn.execute(f"{_APPLICATION} WHERE a.removed = ? ORDER BY last_email DESC, a.id DESC", (int(removed),)).fetchall()


def needs_reply_applications(conn):
    return conn.execute(
        f"{_APPLICATION} WHERE a.removed = 0 AND a.needs_reply_since IS NOT NULL ORDER BY a.needs_reply_since, a.id"
    ).fetchall()


def needs_reply_count(conn):
    """Applications waiting on a reply, plus information requests the person hasn't matched up yet."""
    return conn.execute(
        "SELECT (SELECT COUNT(*) FROM applications WHERE removed = 0 AND needs_reply_since IS NOT NULL)"
        " + (SELECT COUNT(*) FROM emails WHERE state = 'review' AND category = 'info_request')"
    ).fetchone()[0]


def application_emails(conn, application_id):
    return conn.execute("SELECT * FROM emails WHERE application_id = ? ORDER BY date, id", (application_id,)).fetchall()


def change_log(conn, application_id):
    return conn.execute("SELECT * FROM change_log WHERE application_id = ? ORDER BY id", (application_id,)).fetchall()


def review_emails(conn):
    return conn.execute(
        "SELECT * FROM emails WHERE state = 'review' ORDER BY category = 'info_request' DESC, date, id"
    ).fetchall()


def discarded_emails(conn):
    return conn.execute("SELECT * FROM emails WHERE state = 'discarded' ORDER BY date DESC, id").fetchall()
