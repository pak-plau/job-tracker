import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from job_tracker import db, sync, web

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
NOW_TEXT = NOW.isoformat()
SAME_SITE = {"Origin": "http://localhost"}


def email(id, subject="About your application", date="2026-11-01T09:00:00+00:00"):
    return {"id": id, "sender": "Stripe <no-reply@greenhouse.io>", "subject": subject, "date": date, "text": "T"}


def result(category, company="Stripe", role="Backend Engineer"):
    return {"category": category, "company": company, "role": role, "seconds": 1.0}


class WebTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "tracker.db"
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)
        self.start_sync = mock.Mock()
        self.client = web.create_app(self.path, self.start_sync, lambda: NOW).test_client()

    def save(self, id, category, subject="About your application", date="2026-11-01T09:00:00+00:00", **kwargs):
        db.save_email(self.conn, email(id, subject, date), result(category, **kwargs), f"https://mail.example/{id}", NOW_TEXT)

    def page(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def post(self, url, **form):
        return self.client.post(url, data=form, headers=SAME_SITE)

    def application(self):
        return db.applications(self.conn)[0]


class SecurityTest(WebTestCase):
    def test_changes_from_other_sites_are_rejected(self):
        self.save("e1", "confirmation")
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {}):
            response = self.client.post(f"/applications/{self.application()['id']}/remove", headers=headers)
            self.assertEqual(response.status_code, 403, headers)
        self.assertEqual(len(db.applications(self.conn)), 1)

    def test_other_hosts_are_rejected(self):
        response = self.client.get("/", headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_loopback_addresses_are_allowed(self):
        for host in ("127.0.0.1:8173", "localhost:8173"):
            self.assertEqual(self.client.get("/", headers={"Host": host}).status_code, 200)

    def test_server_only_listens_locally(self):
        with mock.patch("flask.Flask.run") as run:
            web.serve()
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")

    def test_email_text_is_always_escaped(self):
        attack = "<script>steal()</script>"
        self.save("e1", "confirmation", subject=attack, company=f"Co {attack}", role=attack)
        self.save("e2", "unclear", subject=attack)
        self.save("e3", "job_alert", subject=attack, company=None, role=None)
        application_id = self.application()["id"]
        for url in ("/", f"/applications/{application_id}", "/review", "/discarded"):
            self.assertNotIn("<script>", self.page(url), url)


class ApplicationsPageTest(WebTestCase):
    def test_empty_tracker_explains_itself(self):
        self.assertIn("No applications yet", self.page("/"))

    def test_lists_applications(self):
        self.save("e1", "confirmation")
        page = self.page("/")
        self.assertIn("Stripe", page)
        self.assertIn("Backend Engineer", page)
        self.assertIn("Applied", page)

    def test_job_alerts_stay_out(self):
        self.save("e1", "job_alert", company="Hooli", role=None)
        self.assertNotIn("Hooli", self.page("/"))

    def test_reply_section_is_hidden_when_empty(self):
        self.save("e1", "confirmation")
        self.assertNotIn("Needs your reply</h2>", self.page("/"))

    def test_reply_section_is_oldest_first_with_wait_link_and_marker(self):
        self.save("e1", "confirmation", role=None)
        self.save("e2", "confirmation", company="Square", role=None)
        self.save("e3", "info_request", date="2026-11-04T00:00:00+00:00", role=None)
        self.save("e4", "info_request", date="2026-11-02T00:00:00+00:00", company="Square", role=None)
        page = self.page("/")
        section = page[page.index("Needs your reply</h2>") :]
        section = section[: section.index("</section>")]
        self.assertLess(section.index("Square"), section.index("Stripe"))
        self.assertIn("3 days", section)
        self.assertIn("https://mail.example/e4", section)
        self.assertIn("Needs reply", page[page.index("</section>") :])

    def test_mark_as_done_clears_it_and_is_logged(self):
        self.save("e1", "confirmation")
        self.save("e2", "info_request")
        application_id = self.application()["id"]
        response = self.post(f"/applications/{application_id}/reply-done")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("Needs your reply</h2>", self.page("/"))
        self.assertEqual(db.change_log(self.conn, application_id)[-1]["who"], "person")

    def test_every_page_shows_the_reply_count(self):
        self.save("e1", "info_request", company=None)
        for url in ("/", "/review", "/discarded", "/sync"):
            self.assertIn("1 needing a reply", self.page(url), url)

    def test_removed_applications_are_hidden_and_can_be_restored(self):
        self.save("e1", "confirmation")
        application_id = self.application()["id"]
        self.post(f"/applications/{application_id}/remove")
        self.assertEqual(db.applications(self.conn), [])
        self.assertIn("Removed applications", self.page("/"))
        self.post(f"/applications/{application_id}/restore")
        self.assertEqual(self.application()["id"], application_id)


class ApplicationDetailTest(WebTestCase):
    def setUp(self):
        super().setUp()
        self.save("e1", "confirmation", subject="Thanks for applying")
        self.save("e2", "interview", subject="Let's talk", date="2026-11-03T09:00:00+00:00")
        self.id = self.application()["id"]

    def edit(self, **changes):
        form = {"company": "Stripe", "role": "Backend Engineer", "status": "Interviewing", **changes}
        return self.post(f"/applications/{self.id}", **form)

    def test_timeline_shows_emails_links_and_changes_in_order(self):
        page = self.page(f"/applications/{self.id}")
        self.assertLess(page.index("Thanks for applying"), page.index("Let&#39;s talk"))
        self.assertIn("https://mail.example/e2", page)
        self.assertIn("Applied", page)
        self.assertIn("Interviewing", page)

    def test_missing_application_is_not_found(self):
        self.assertEqual(self.client.get("/applications/999").status_code, 404)
        self.assertEqual(self.post("/applications/999", company="X", status="Applied").status_code, 404)

    def test_edit_saves_and_is_logged_as_the_person(self):
        self.assertEqual(self.edit(company="Stripe, Inc.", status="Offer").status_code, 302)
        updated = self.application()
        self.assertEqual((updated["company"], updated["status"]), ("Stripe, Inc.", "Offer"))
        self.assertEqual(db.change_log(self.conn, self.id)[-1]["who"], "person")
        self.assertIn("Stripe, Inc.", self.page(f"/applications/{self.id}"))

    def test_edit_can_set_needs_reply_by_hand_and_keep_it(self):
        self.edit(needs_reply="on")
        self.assertEqual(self.application()["needs_reply_since"], NOW_TEXT)
        self.assertIn("checked", self.page(f"/applications/{self.id}"))
        self.edit(needs_reply="on")
        self.assertEqual(self.application()["needs_reply_since"], NOW_TEXT)
        self.edit()
        self.assertIsNone(self.application()["needs_reply_since"])

    def test_blank_role_becomes_unknown(self):
        self.edit(role="   ")
        self.assertIsNone(self.application()["role"])

    def test_invalid_edits_are_rejected(self):
        self.assertEqual(self.edit(company="  ").status_code, 400)
        self.assertEqual(self.edit(status="Hired").status_code, 400)
        self.assertEqual(self.application()["status"], "Interviewing")


class ReviewPageTest(WebTestCase):
    def setUp(self):
        super().setUp()
        self.save("e1", "confirmation")
        self.id = self.application()["id"]

    def test_empty_list_says_so(self):
        self.assertIn("Nothing to review", self.page("/review"))

    def test_shows_reason_and_gmail_link_with_information_requests_first(self):
        self.save("a", "unclear", subject="Older unclear")
        self.save("b", "info_request", subject="Please send your transcript", company=None, date="2026-11-04T00:00:00+00:00")
        page = self.page("/review")
        self.assertLess(page.index("Please send your transcript"), page.index("Older unclear"))
        self.assertIn("couldn&#39;t tell", page)
        self.assertIn("https://mail.example/a", page)

    def test_tab_shows_how_many_are_waiting(self):
        self.save("a", "unclear")
        self.assertIn("Review List (1)", self.page("/"))

    def test_attach(self):
        self.save("a", "rejection", company="Hiring System")
        self.assertEqual(self.post("/review/a/attach", application_id=str(self.id)).status_code, 302)
        self.assertEqual(self.application()["status"], "Rejected")
        self.assertEqual(db.review_emails(self.conn), [])

    def test_attach_rejects_a_bad_application(self):
        self.save("a", "unclear")
        self.assertEqual(self.post("/review/a/attach", application_id="nope").status_code, 400)
        self.assertEqual(self.post("/review/a/attach").status_code, 400)

    def test_company_match_is_preselected_for_attach(self):
        self.save("a", "unclear", company="Stripe")
        self.assertIn(f'value="{self.id}" selected', self.page("/review"))

    def test_create_prefilled_from_the_email(self):
        self.save("a", "rejection", company="Square", role=None)
        self.assertIn('value="Square"', self.page("/review"))
        response = self.post("/review/a/create", company="Square", role="")
        self.assertEqual(response.status_code, 302)
        created = next(a for a in db.applications(self.conn) if a["company"] == "Square")
        self.assertEqual((created["status"], created["role"]), ("Rejected", None))

    def test_create_needs_a_company(self):
        self.save("a", "unclear", company=None)
        self.assertEqual(self.post("/review/a/create", company=" ", role="").status_code, 400)
        self.assertEqual(len(db.review_emails(self.conn)), 1)

    def test_dismiss_then_send_back_from_discarded(self):
        self.save("a", "unclear", subject="Odd one")
        self.post("/review/a/dismiss")
        self.assertNotIn("Odd one", self.page("/review"))
        self.assertIn("Odd one", self.page("/discarded"))
        self.post("/discarded/a/send-to-review")
        self.assertIn("Odd one", self.page("/review"))
        self.assertNotIn("Odd one", self.page("/discarded"))


class DiscardedPageTest(WebTestCase):
    def test_empty_list_says_so(self):
        self.assertIn("No discarded emails", self.page("/discarded"))

    def test_lists_job_alerts_with_their_category_and_link(self):
        self.save("a", "job_alert", subject="10 new roles", company=None, role=None)
        page = self.page("/discarded")
        self.assertIn("10 new roles", page)
        self.assertIn("Job alert", page)
        self.assertIn("https://mail.example/a", page)

    def test_misfiled_job_alert_can_become_an_application(self):
        self.save("a", "job_alert", subject="Application received", company=None, role=None)
        self.post("/discarded/a/send-to-review")
        self.post("/review/a/create", company="Stripe", role="")
        self.assertEqual(self.application()["company"], "Stripe")


class SyncPageTest(WebTestCase):
    def finished_run(self, status="succeeded", error=None, at=NOW):
        run_id = sync.begin_run(self.conn, at)
        sync._finish(self.conn, run_id, status, error, at)
        return run_id

    def test_warns_that_it_can_take_minutes(self):
        self.assertIn("minutes", self.page("/sync"))

    def test_button_starts_a_sync_when_idle(self):
        response = self.post("/sync")
        self.assertEqual(response.status_code, 302)
        self.start_sync.assert_called_once()

    def test_button_does_not_start_a_second_sync(self):
        sync.begin_run(self.conn, NOW)
        self.post("/sync")
        self.start_sync.assert_not_called()

    def test_running_sync_shows_progress_and_keeps_refreshing(self):
        run_id = sync.begin_run(self.conn, NOW)
        self.conn.execute("UPDATE sync_runs SET done = 3, total = 10 WHERE id = ?", (run_id,))
        page = self.page("/sync")
        self.assertIn('http-equiv="refresh"', page)
        self.assertIn("3 of 10", page)

    def test_refreshes_after_the_button_until_the_new_run_appears(self):
        self.finished_run()
        response = self.post("/sync")
        page = self.page(response.headers["Location"])
        self.assertIn('http-equiv="refresh"', page)
        self.finished_run()
        self.assertNotIn('http-equiv="refresh"', self.page(response.headers["Location"]))

    def test_finished_sync_stops_refreshing(self):
        self.finished_run()
        self.assertNotIn('http-equiv="refresh"', self.page("/sync"))

    def test_crashed_sync_stops_refreshing_and_can_be_restarted(self):
        sync.begin_run(self.conn, NOW - sync.STALE_AFTER - timedelta(seconds=1))
        self.assertNotIn('http-equiv="refresh"', self.page("/sync"))
        self.post("/sync")
        self.start_sync.assert_called_once()

    def test_failure_message_shows_on_every_page(self):
        self.finished_run("failed", "Your Google sign-in has expired.")
        for url in ("/", "/review", "/discarded", "/sync"):
            self.assertIn("Your Google sign-in has expired.", self.page(url), url)

    def test_success_clears_the_failure_message(self):
        self.finished_run("failed", "Your Google sign-in has expired.")
        self.finished_run()
        self.assertNotIn("sign-in has expired", self.page("/"))

    def test_warns_after_48_hours_without_a_successful_sync(self):
        self.finished_run(at=NOW - timedelta(hours=49))
        self.assertIn("hasn&#39;t synced", self.page("/"))

    def test_no_warning_after_a_recent_sync(self):
        self.finished_run(at=NOW - timedelta(hours=47))
        self.assertNotIn("hasn&#39;t synced", self.page("/"))

    def test_page_suggests_a_first_sync(self):
        self.assertIn("hasn&#39;t synced yet", self.page("/sync"))


if __name__ == "__main__":
    unittest.main()
