"""Read new job email from Gmail, classify it and save the result. Terms are defined in GLOSSARY.md."""
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from job_tracker import db
from job_tracker.classify import ClassifyError, UnusableAnswer
from job_tracker.gmail import SEARCH, SIGN_IN_EXPIRED, SignInNeeded

FIRST_RUN_DAYS = 90
DAILY_SYNC_HOUR = 8
STALE_WARNING = timedelta(hours=48)
OVERLAP = timedelta(days=1)  # Gmail can index mail late. Rereading a day is safe since processed email is skipped.
HEARTBEAT_SECONDS = 15
STALE_AFTER = timedelta(seconds=60)


class AlreadyRunning(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def run_sync(path, open_mailbox, classify_email, days=None, only_if_due=False, now=utc_now):
    """Process all new mail, returning {ok, message}. Whatever saved before a failure stays saved.

    days can only look further back. If a run with days fails, repeat it with the same days to finish it.
    """
    conn = db.connect(path)
    try:
        last = last_success(conn)
        if only_if_due and not is_due(last, now()):
            return {"ok": True, "message": "Already synced today."}
        try:
            run_id = begin_run(conn, now())
        except AlreadyRunning:
            return {"ok": False, "message": "A sync is already running."}
        with _heartbeat(path, run_id, now):
            try:
                mailbox = open_mailbox()
                query = f"{SEARCH} after:{int(_search_start(last, days, now()).timestamp())}"
                count = _process_new_mail(conn, run_id, mailbox, classify_email, query, now)
            except BaseException as error:
                message = _plain_error(error, path)
                _finish(conn, run_id, "failed", message, now())
                if not isinstance(error, Exception):
                    raise
                return {"ok": False, "message": message}
        if count is None:
            return {"ok": False, "message": "This sync stopped because it was marked as crashed after a long pause."}
        _finish(conn, run_id, "succeeded", None, now())
        return {"ok": True, "message": f"Processed {count} new emails."}
    finally:
        conn.close()


def _process_new_mail(conn, run_id, mailbox, classify_email, query, now):
    """Return how many emails were processed, or None if another sync took over."""
    new = [email_id for email_id in reversed(list(mailbox.ids(query))) if not db.is_processed(conn, email_id)]
    conn.execute("UPDATE sync_runs SET total = ? WHERE id = ?", (len(new), run_id))
    for done, email_id in enumerate(new):
        if conn.execute("SELECT status FROM sync_runs WHERE id = ?", (run_id,)).fetchone()[0] != "running":
            return None
        email = mailbox.fetch(email_id)
        try:
            result = classify_email(email)
        except UnusableAnswer:
            result = None  # goes to the Review List, because stopping would block every later email forever
        db.save_email(conn, email, result, mailbox.link(email_id), now().isoformat())
        conn.execute("UPDATE sync_runs SET done = ? WHERE id = ?", (done + 1, run_id))
    return len(new)


def _search_start(last, days, now):
    start = datetime.fromisoformat(last) - OVERLAP if last else now - timedelta(days=FIRST_RUN_DAYS)
    return min(start, now - timedelta(days=days)) if days else start


def _plain_error(error, path):
    from google.auth.exceptions import RefreshError
    from googleapiclient.errors import HttpError

    if isinstance(error, (SignInNeeded, ClassifyError, SystemExit)):
        return str(error)
    if isinstance(error, RefreshError):
        return SIGN_IN_EXPIRED
    if isinstance(error, HttpError):
        return f"Gmail returned an error ({error.status_code}). Try again later. Everything already processed is saved."
    if isinstance(error, KeyboardInterrupt):
        return "The sync was stopped. Everything already processed is saved."
    # The details stay in a local file because they can quote email content.
    log = path.with_name("sync-error.log")
    log.write_text(traceback.format_exc(), encoding="utf-8")
    return (
        f"The sync stopped because of an unexpected problem ({type(error).__name__}). Check your internet connection "
        f"and try again. Everything already processed is saved. Details are in {log}."
    )


# One sync at a time, however it was started. A sync proves it's alive with a heartbeat,
# so a crashed one stops blocking the next as soon as its heartbeat goes quiet.

def begin_run(conn, now):
    with db.transaction(conn):
        for run in conn.execute("SELECT id, heartbeat_at FROM sync_runs WHERE status = 'running'").fetchall():
            if now - datetime.fromisoformat(run["heartbeat_at"]) <= STALE_AFTER:
                raise AlreadyRunning
            conn.execute(
                "UPDATE sync_runs SET status = 'crashed', finished_at = ?, error = ? WHERE id = ?",
                (now.isoformat(), "This sync stopped without finishing. Everything it processed is saved.", run["id"]),
            )
        return conn.execute(
            "INSERT INTO sync_runs (status, started_at, heartbeat_at) VALUES ('running', ?, ?)",
            (now.isoformat(), now.isoformat()),
        ).lastrowid


def beat(conn, run_id, now):
    conn.execute("UPDATE sync_runs SET heartbeat_at = ? WHERE id = ? AND status = 'running'", (now.isoformat(), run_id))


@contextmanager
def _heartbeat(path, run_id, now):
    # On its own thread, since one email can take minutes while the model loads.
    stop = threading.Event()

    def keep_beating():
        conn = db.connect(path)
        try:
            while not stop.wait(HEARTBEAT_SECONDS):
                beat(conn, run_id, now())
        finally:
            conn.close()

    thread = threading.Thread(target=keep_beating, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def _finish(conn, run_id, status, error, now):
    conn.execute(
        "UPDATE sync_runs SET status = ?, error = ?, finished_at = ? WHERE id = ? AND status = 'running'",
        (status, error, now.isoformat(), run_id),
    )


def latest_run(conn):
    return conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()


def is_running(conn, now):
    """Whether a sync is really running, so the page doesn't wait on one that crashed."""
    run = latest_run(conn)
    return bool(run) and run["status"] == "running" and now - datetime.fromisoformat(run["heartbeat_at"]) <= STALE_AFTER


def last_success(conn):
    run = conn.execute("SELECT started_at FROM sync_runs WHERE status = 'succeeded' ORDER BY id DESC LIMIT 1").fetchone()
    return run["started_at"] if run else None


def is_due(last, now):
    """Whether the daily sync has happened since the latest 8:00 AM, local time, which also gives catch-up."""
    local = now.astimezone()
    latest_slot = local.replace(hour=DAILY_SYNC_HOUR, minute=0, second=0, microsecond=0)
    if local < latest_slot:
        latest_slot -= timedelta(days=1)
    return last is None or datetime.fromisoformat(last) < latest_slot


def is_stale(last, now):
    return last is not None and now - datetime.fromisoformat(last) > STALE_WARNING
