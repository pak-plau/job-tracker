"""The tracker's web page, for this computer only. Email text is untrusted, and templates escape all of it."""
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, g, redirect, render_template, request, url_for

from job_tracker import db, sync
from job_tracker.classify import normalize_company

PORT = 8173
ROOT = Path(__file__).parent.parent
CATEGORY_NAMES = {
    "confirmation": "Confirmation",
    "interview": "Interview invitation",
    "info_request": "Information request",
    "rejection": "Rejection",
    "offer": "Offer",
    "unclear": "Unclear",
    "job_alert": "Job alert",
    "irrelevant": "Not relevant",
}


def start_sync_process():
    # A separate process, so a long sync doesn't depend on the page staying open.
    subprocess.Popen(
        [sys.executable, "-m", "job_tracker", "sync"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def create_app(path=db.DB_FILE, start_sync=start_sync_process, now=sync.utc_now):
    app = Flask(__name__)

    @app.before_request
    def only_this_computer():
        # Checking the host stops a malicious site reaching this page by pointing its own domain at 127.0.0.1.
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            abort(403)
        if request.method == "POST" and urlsplit(request.headers.get("Origin", "")).netloc != request.host:
            abort(403)

    def conn():
        if "conn" not in g:
            g.conn = db.connect(path)
        return g.conn

    @app.teardown_appcontext
    def close_conn(error):
        if "conn" in g:
            g.conn.close()

    @app.context_processor
    def top_bar():
        run = sync.latest_run(conn())
        warnings = []
        if run and run["status"] in ("failed", "crashed"):
            warnings.append(run["error"])
        if sync.is_stale(sync.last_success(conn()), now()):
            warnings.append("The tracker hasn't synced successfully in over 48 hours, so new email may be missing.")
        return {
            "reply_count": db.needs_reply_count(conn()),
            "review_count": len(db.review_emails(conn())),
            "warnings": warnings,
            "category_names": CATEGORY_NAMES,
        }

    @app.template_filter("day")
    def day(value):
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d")

    @app.template_filter("waited")
    def waited(value):
        days = (now() - datetime.fromisoformat(value)).days
        return "today" if days < 1 else "1 day" if days == 1 else f"{days} days"

    @app.route("/")
    def applications():
        return render_template(
            "applications.html",
            applications=db.applications(conn()),
            replies=db.needs_reply_applications(conn()),
            removed=db.applications(conn(), removed=True),
        )

    @app.route("/applications/<int:application_id>")
    def application(application_id):
        return render_template(
            "application.html",
            application=_existing(conn(), application_id),
            timeline=_timeline(conn(), application_id),
            statuses=db.STATUSES,
        )

    @app.post("/applications/<int:application_id>")
    def edit_application(application_id):
        _existing(conn(), application_id)
        status = request.form.get("status")
        if status not in db.STATUSES:
            abort(400, "That isn't a valid status.")
        db.edit_application(
            conn(),
            application_id,
            _required_company(),
            _optional_role(),
            status,
            "needs_reply" in request.form,
            now().isoformat(),
        )
        return redirect(url_for("application", application_id=application_id))

    @app.post("/applications/<int:application_id>/reply-done")
    def reply_done(application_id):
        db.mark_reply_done(conn(), application_id, now().isoformat())
        return redirect(url_for("applications"))

    @app.post("/applications/<int:application_id>/remove")
    def remove(application_id):
        db.set_removed(conn(), application_id, True, now().isoformat())
        return redirect(url_for("applications"))

    @app.post("/applications/<int:application_id>/restore")
    def restore(application_id):
        db.set_removed(conn(), application_id, False, now().isoformat())
        return redirect(url_for("application", application_id=application_id))

    @app.route("/review")
    def review():
        choices = db.applications(conn())
        choices = sorted(choices, key=lambda a: (a["company"].casefold(), (a["role"] or "").casefold()))
        return render_template(
            "review.html",
            emails=[{"email": e, "preselected": _same_company(e, choices)} for e in db.review_emails(conn())],
            choices=choices,
        )

    @app.post("/review/<email_id>/attach")
    def attach(email_id):
        application_id = request.form.get("application_id", type=int)
        if application_id is None:
            abort(400, "Choose an application to attach this email to.")
        db.attach_email(conn(), email_id, application_id, now().isoformat())
        return redirect(url_for("review"))

    @app.post("/review/<email_id>/create")
    def create(email_id):
        db.create_from_email(conn(), email_id, _required_company(), _optional_role(), now().isoformat())
        return redirect(url_for("review"))

    @app.post("/review/<email_id>/dismiss")
    def dismiss(email_id):
        db.dismiss_email(conn(), email_id)
        return redirect(url_for("review"))

    @app.route("/discarded")
    def discarded():
        return render_template("discarded.html", emails=db.discarded_emails(conn()))

    @app.post("/discarded/<email_id>/send-to-review")
    def send_to_review(email_id):
        db.send_to_review(conn(), email_id)
        return redirect(url_for("discarded"))

    @app.route("/sync")
    def sync_page():
        run = sync.latest_run(conn())
        running = sync.is_running(conn(), now())
        # After the button, the new process takes a moment to record its run.
        # shortcut: if that process dies before recording it, this waits until the page is left, fix if ever seen.
        starting = request.args.get("after", type=int) == (run["id"] if run else 0)
        last = sync.last_success(conn())
        return render_template(
            "sync.html",
            run=run,
            running=running,
            refresh=running or starting,
            last_success=last,
            first_sync_note=None if last else "The tracker hasn't synced yet. The first sync reads the last 90 days of job email.",
        )

    @app.post("/sync")
    def sync_now():
        if sync.is_running(conn(), now()):
            return redirect(url_for("sync_page"))
        run = sync.latest_run(conn())
        start_sync()
        return redirect(url_for("sync_page", after=run["id"] if run else 0))

    return app


def _existing(conn, application_id):
    found = db.application(conn, application_id)
    if found is None:
        abort(404, "That application doesn't exist.")
    return found


def _required_company():
    company = request.form.get("company", "").strip()
    if not company:
        abort(400, "Company can't be blank.")
    return company


def _optional_role():
    return request.form.get("role", "").strip() or None


def _same_company(email, choices):
    company = normalize_company(email["company"])
    if company:
        for choice in choices:
            if normalize_company(choice["company"]) == company:
                return choice["id"]
    return None


def _timeline(conn, application_id):
    """Emails with the changes they caused, and the person's own changes, oldest first."""
    changes = [{**change, "text": _describe(change)} for change in db.change_log(conn, application_id)]
    items = [
        {"when": email["date"], "email": email, "changes": [c for c in changes if c["email_id"] == email["id"]]}
        for email in db.application_emails(conn, application_id)
    ]
    items += [{"when": c["at"], "email": None, "changes": [c]} for c in changes if not c["email_id"]]
    return sorted(items, key=lambda item: item["when"])


def _describe(change):
    field, old, new = change["field"], change["old_value"], change["new_value"]
    who = "you" if change["who"] == "person" else "the tracker"
    if field == "created":
        return f"Application created as {new} by {who}"
    if field == "repeat":
        return f"Repeat of an earlier email ({CATEGORY_NAMES.get(new, new)}), so nothing changed"
    if field == "attached":
        return f"Email attached by {who}"
    if field == "needs_reply":
        return f"Needs Reply turned {'on' if new == 'yes' else 'off'} by {who}"
    if field == "removed":
        return f"{'Removed' if new == 'yes' else 'Restored'} by {who}"
    return f"{field.capitalize()} changed from {old or 'unknown'} to {new or 'unknown'} by {who}"


def serve():
    print(f"Open http://127.0.0.1:{PORT} in your browser. Press Ctrl+C to stop.")
    create_app().run(host="127.0.0.1", port=PORT)
