"""Run: python -m job_tracker sync [--days N] [--if-due] | serve | schedule"""
import argparse
import sys

from job_tracker.classify import DEFAULT_MODEL, classify
from job_tracker.db import DB_FILE
from job_tracker.gmail import open_mailbox
from job_tracker.sync import run_sync


def main():
    parser = argparse.ArgumentParser(prog="python -m job_tracker", description="Track job applications from Gmail.")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="process new job email")
    sync.add_argument("--days", type=int, help="look this many days back, to go further than the first 90")
    sync.add_argument("--if-due", action="store_true", help="do nothing if already synced since the latest 8:00 AM")
    commands.add_parser("serve", help="open the tracker page")
    commands.add_parser("schedule", help="sync daily at 8:00 AM, catching up if the computer was off")
    args = parser.parse_args()

    if args.command == "serve":
        from job_tracker.web import serve

        serve()
        return 0
    if args.command == "schedule":
        from job_tracker.schedule import install

        return install()

    # Only a person at a terminal can complete a browser sign-in. The schedule and the page can't.
    interactive = bool(sys.stdin and sys.stdin.isatty())
    outcome = run_sync(
        DB_FILE,
        lambda: open_mailbox(interactive),
        lambda email: classify(email, DEFAULT_MODEL),
        days=args.days,
        only_if_due=args.if_due,
    )
    print(outcome["message"])
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
