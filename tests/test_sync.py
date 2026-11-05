import io
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

from job_tracker import __main__ as command, db, gmail, schedule, sync, web
from job_tracker.classify import ClassifyError, UnusableAnswer
from job_tracker.gmail import SignInNeeded

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


class FakeMailbox:
    def __init__(self, ids=()):
        self.newest_first = list(ids)
        self.queries = []
        self.fetched = []

    def ids(self, query):
        self.queries.append(query)
        return iter(self.newest_first)

    def fetch(self, email_id):
        self.fetched.append(email_id)
        return {"id": email_id, "sender": "a@b.c", "subject": email_id, "date": "2026-11-01T00:00:00+00:00", "text": "T"}

    def link(self, email_id):
        return f"https://gmail/{email_id}"


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "tracker.db"
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)

    def run_sync(self, mailbox=None, classify=None, now=NOW, **kwargs):
        mailbox = mailbox or FakeMailbox()
        classify = classify or (lambda email: {"category": "irrelevant", "company": None, "role": None})
        return sync.run_sync(self.path, lambda: mailbox, classify, now=lambda: now, **kwargs)

    def latest(self):
        return sync.latest_run(self.conn)


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ProgressBarTest(SyncTestCase):
    BAR_DONE = "[" + "#" * 30 + "] 2/2 emails (100%)"

    def run_command(self, stdout, ids=("new", "old"), classify=None):
        classify = classify or (lambda email, model: {"category": "irrelevant", "company": None, "role": None})
        with (
            mock.patch.object(command, "DB_FILE", self.path),
            mock.patch.object(command, "open_mailbox", lambda interactive: FakeMailbox(ids)),
            mock.patch.object(command, "classify", classify),
            mock.patch("sys.argv", ["job_tracker", "sync"]),
            mock.patch("sys.stdout", stdout),
        ):
            return command.main()

    def test_reports_progress_from_zero_to_the_total(self):
        calls = []
        self.run_sync(FakeMailbox(["new", "old"]), on_progress=lambda done, total: calls.append((done, total)))
        self.assertEqual(calls, [(0, 2), (1, 2), (2, 2)])

    def test_terminal_shows_a_filling_bar_then_the_result_on_its_own_line(self):
        terminal = Terminal()
        self.assertEqual(self.run_command(terminal), 0)
        self.assertEqual(
            terminal.getvalue().split("\r")[1:],
            [
                "[" + "-" * 30 + "] 0/2 emails (0%)",
                "[" + "#" * 15 + "-" * 15 + "] 1/2 emails (50%)",
                self.BAR_DONE + "\nProcessed 2 new emails.\n",
            ],
        )

    def test_failure_message_starts_on_its_own_line(self):
        def fail(email, model):
            raise ClassifyError("Ollama isn't running.")

        terminal = Terminal()
        self.assertEqual(self.run_command(terminal, classify=fail), 1)
        self.assertTrue(terminal.getvalue().endswith("0/2 emails (0%)\nOllama isn't running.\n"))

    def test_no_new_email_draws_no_bar(self):
        terminal = Terminal()
        self.run_command(terminal, ids=())
        self.assertEqual(terminal.getvalue(), "Processed 0 new emails.\n")

    def test_output_going_to_a_file_gets_only_the_result(self):
        output = io.StringIO()
        self.run_command(output)
        self.assertEqual(output.getvalue(), "Processed 2 new emails.\n")

    def test_scheduled_sync_with_no_output_at_all_still_runs(self):
        self.assertEqual(self.run_command(None), 0)  # pythonw gives the process no stdout
        self.assertEqual(self.latest()["status"], "succeeded")

    def test_sync_started_by_the_page_cannot_ask_for_a_browser_sign_in(self):
        with mock.patch("subprocess.Popen") as popen:
            web.start_sync_process()
        # Runs a real process, because Windows counts the null device as a terminal.
        check = "import sys; sys.exit(sys.stdin.isatty())"
        child = subprocess.run([sys.executable, "-c", check], stdin=popen.call_args.kwargs["stdin"])
        self.assertEqual(child.returncode, 0)


class RunSyncTest(SyncTestCase):
    def test_processes_oldest_first_and_saves_progress(self):
        mailbox = FakeMailbox(["new", "middle", "old"])
        outcome = self.run_sync(mailbox)
        self.assertEqual(mailbox.fetched, ["old", "middle", "new"])
        self.assertTrue(outcome["ok"])
        run = self.latest()
        self.assertEqual((run["status"], run["done"], run["total"]), ("succeeded", 3, 3))

    def test_saves_classified_results(self):
        classify = lambda email: {"category": "confirmation", "company": "Stripe", "role": None}
        self.run_sync(FakeMailbox(["a"]), classify)
        self.assertEqual(db.applications(self.conn)[0]["company"], "Stripe")
        self.assertEqual(db.application_emails(self.conn, 1)[0]["link"], "https://gmail/a")

    def test_processed_emails_are_never_reclassified(self):
        self.run_sync(FakeMailbox(["a"]))
        mailbox = FakeMailbox(["b", "a"])
        self.run_sync(mailbox, now=NOW + timedelta(days=1))
        self.assertEqual(mailbox.fetched, ["b"])

    def test_first_run_goes_back_90_days(self):
        mailbox = FakeMailbox()
        self.run_sync(mailbox)
        self.assertIn(f"after:{int((NOW - timedelta(days=90)).timestamp())}", mailbox.queries[0])
        self.assertIn(gmail.SEARCH, mailbox.queries[0])

    def test_days_option_goes_further_back_on_the_first_run(self):
        mailbox = FakeMailbox()
        self.run_sync(mailbox, days=365)
        self.assertIn(f"after:{int((NOW - timedelta(days=365)).timestamp())}", mailbox.queries[0])

    def test_later_run_overlaps_the_last_success_by_a_day(self):
        self.run_sync()
        mailbox = FakeMailbox()
        self.run_sync(mailbox, now=NOW + timedelta(days=3))
        self.assertIn(f"after:{int((NOW - timedelta(days=1)).timestamp())}", mailbox.queries[0])

    def test_days_option_can_extend_but_never_shrink_a_later_run(self):
        self.run_sync()
        later = NOW + timedelta(days=10)
        shrink = FakeMailbox()
        self.run_sync(shrink, now=later, days=2)
        self.assertIn(f"after:{int((NOW - timedelta(days=1)).timestamp())}", shrink.queries[0])
        extend = FakeMailbox()
        self.run_sync(extend, now=later, days=365)
        self.assertIn(f"after:{int((later - timedelta(days=365)).timestamp())}", extend.queries[0])

    def test_failed_sync_resumes_from_the_last_success_without_missing_mail(self):
        self.run_sync()
        def broken():
            raise SignInNeeded("Sign in again")
        outcome = sync.run_sync(self.path, broken, None, now=lambda: NOW + timedelta(days=9))
        self.assertFalse(outcome["ok"])
        mailbox = FakeMailbox()
        self.run_sync(mailbox, now=NOW + timedelta(days=10))
        self.assertIn(f"after:{int((NOW - timedelta(days=1)).timestamp())}", mailbox.queries[0])

    def test_stop_partway_keeps_what_was_saved_and_finishes_next_time(self):
        def classify(email):
            if email["id"] == "new":
                raise ClassifyError("Could not reach Ollama. Is it running?")
            return {"category": "irrelevant", "company": None, "role": None}

        outcome = self.run_sync(FakeMailbox(["new", "old"]), classify)
        self.assertFalse(outcome["ok"])
        self.assertIn("Ollama", outcome["message"])
        self.assertEqual(self.latest()["status"], "failed")
        self.assertTrue(db.is_processed(self.conn, "old"))
        self.assertFalse(db.is_processed(self.conn, "new"))

        mailbox = FakeMailbox(["new", "old"])
        self.assertTrue(self.run_sync(mailbox)["ok"])
        self.assertEqual(mailbox.fetched, ["new"])

    def test_unusable_answer_goes_to_review_and_does_not_stop_the_sync(self):
        def classify(email):
            raise UnusableAnswer("bad answer")

        self.assertTrue(self.run_sync(FakeMailbox(["a", "b"]), classify)["ok"])
        self.assertEqual(len(db.review_emails(self.conn)), 2)

    def test_sign_in_problem_has_a_plain_message(self):
        def broken():
            raise SignInNeeded("Your Google sign-in has expired.")

        outcome = sync.run_sync(self.path, broken, None, now=lambda: NOW)
        self.assertEqual(outcome["message"], "Your Google sign-in has expired.")
        self.assertEqual(self.latest()["error"], "Your Google sign-in has expired.")

    def test_revoked_google_token_during_a_sync_asks_for_sign_in(self):
        from google.auth.exceptions import RefreshError

        class Revoked(FakeMailbox):
            def ids(self, query):
                raise RefreshError("invalid_grant: Token has been expired or revoked.")

        outcome = self.run_sync(Revoked())
        self.assertIn("sign-in", outcome["message"])
        self.assertNotIn("invalid_grant", outcome["message"])

    def test_unexpected_error_has_a_plain_message_without_details(self):
        class Broken(FakeMailbox):
            def ids(self, query):
                raise RuntimeError("private details")

        outcome = self.run_sync(Broken())
        self.assertFalse(outcome["ok"])
        self.assertIn("RuntimeError", outcome["message"])
        self.assertNotIn("private details", outcome["message"])


class OneAtATimeTest(SyncTestCase):
    def test_second_sync_is_refused_while_one_is_running(self):
        sync.begin_run(self.conn, NOW)
        mailbox = FakeMailbox(["a"])
        outcome = self.run_sync(mailbox, now=NOW + timedelta(seconds=30))
        self.assertFalse(outcome["ok"])
        self.assertIn("already running", outcome["message"])
        self.assertEqual(mailbox.fetched, [])
        self.assertEqual(self.latest()["status"], "running")

    def test_crashed_sync_does_not_block_later_ones(self):
        crashed = sync.begin_run(self.conn, NOW)
        outcome = self.run_sync(now=NOW + sync.STALE_AFTER + timedelta(seconds=1))
        self.assertTrue(outcome["ok"])
        status = self.conn.execute("SELECT status FROM sync_runs WHERE id = ?", (crashed,)).fetchone()[0]
        self.assertEqual(status, "crashed")

    def test_heartbeat_keeps_a_slow_sync_alive(self):
        run_id = sync.begin_run(self.conn, NOW)
        sync.beat(self.conn, run_id, NOW + sync.STALE_AFTER)
        with self.assertRaises(sync.AlreadyRunning):
            sync.begin_run(self.conn, NOW + sync.STALE_AFTER + timedelta(seconds=1))

    def test_sync_that_lost_its_slot_stops_without_overwriting_the_new_one(self):
        def classify(email):
            # Simulates this run waking from a long sleep after being marked as crashed.
            self.conn.execute("UPDATE sync_runs SET status = 'crashed'")
            return {"category": "irrelevant", "company": None, "role": None}

        mailbox = FakeMailbox(["new", "old"])
        outcome = self.run_sync(mailbox, classify)
        self.assertFalse(outcome["ok"])
        self.assertEqual(mailbox.fetched, ["old"])
        self.assertEqual(self.latest()["status"], "crashed")


class DueTest(unittest.TestCase):
    def at(self, hour, day=5):
        return datetime(2026, 11, day, hour, 30).astimezone()

    def test_never_synced_is_due(self):
        self.assertTrue(sync.is_due(None, self.at(3)))

    def test_due_after_8am_when_not_yet_synced_today(self):
        self.assertTrue(sync.is_due(self.at(7).isoformat(), self.at(9)))

    def test_not_due_again_after_syncing_today(self):
        self.assertFalse(sync.is_due(self.at(8).isoformat(), self.at(23)))

    def test_not_due_before_8am_when_synced_after_yesterdays_8am(self):
        self.assertFalse(sync.is_due(self.at(20, day=4).isoformat(), self.at(6)))

    def test_catches_up_when_the_computer_was_off_at_8am(self):
        self.assertTrue(sync.is_due(self.at(9, day=2).isoformat(), self.at(6)))

    def test_only_if_due_skips_without_starting_a_run(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "tracker.db"
        now = self.at(9)
        self.assertTrue(sync.run_sync(path, FakeMailbox, lambda e: None, now=lambda: now)["ok"])
        opened = mock.Mock()
        outcome = sync.run_sync(path, opened, None, only_if_due=True, now=lambda: now + timedelta(hours=1))
        self.assertTrue(outcome["ok"])
        opened.assert_not_called()


class StaleTest(unittest.TestCase):
    def test_warns_only_after_48_hours_without_a_success(self):
        self.assertFalse(sync.is_stale(None, NOW))
        self.assertFalse(sync.is_stale((NOW - timedelta(hours=47)).isoformat(), NOW))
        self.assertTrue(sync.is_stale((NOW - timedelta(hours=49)).isoformat(), NOW))


class ScheduleTest(unittest.TestCase):
    def test_task_checks_hourly_catches_up_and_escapes_paths(self):
        xml = schedule.task_xml(Path("C:/A & B/pythonw.exe"), Path("C:/A & B"))
        task = ElementTree.fromstring(xml.encode("utf-16"))
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(task.find(".//t:Interval", ns).text, "PT1H")
        self.assertEqual(task.find(".//t:StartWhenAvailable", ns).text, "true")
        self.assertEqual(task.find(".//t:Arguments", ns).text, "-m job_tracker sync --if-due")
        self.assertIn("A & B", task.find(".//t:Command", ns).text)
        self.assertIn("A & B", task.find(".//t:WorkingDirectory", ns).text)


class ConnectTest(unittest.TestCase):
    def test_without_a_browser_a_missing_sign_in_raises_a_plain_error(self):
        with mock.patch.object(gmail, "TOKEN_FILE", Path("does-not-exist.json")):
            with self.assertRaises(SignInNeeded):
                gmail.connect(interactive=False)

    def test_link_opens_the_right_account(self):
        self.assertEqual(
            gmail.Mailbox(None, "a+b@example.com").link("abc"),
            "https://mail.google.com/mail/?authuser=a%2Bb%40example.com#all/abc",
        )


if __name__ == "__main__":
    unittest.main()
