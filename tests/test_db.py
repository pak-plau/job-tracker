import tempfile
import unittest
from pathlib import Path

from job_tracker import db

NOW = "2026-11-05T12:00:00+00:00"


def email(id="e1", subject="About your application", date="2026-11-01T09:00:00+00:00"):
    return {
        "id": id,
        "sender": "Stripe <no-reply@greenhouse.io>",
        "subject": subject,
        "date": date,
        "text": "SECRET EMAIL BODY",
    }


def result(category, company="Stripe", role="Backend Engineer"):
    return {"category": category, "company": company, "role": role, "seconds": 1.0}


class DbTestCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "nested" / "tracker.db"
        self.conn = db.connect(self.path)
        self.addCleanup(lambda: self.conn.close())

    def save(self, id, category, **kwargs):
        db.save_email(self.conn, email(id), result(category, **kwargs), f"https://gmail/{id}", NOW)

    def only_application(self):
        applications = db.applications(self.conn)
        self.assertEqual(len(applications), 1)
        return applications[0]

    def changes(self, application_id):
        return [(c["field"], c["old_value"], c["new_value"], c["who"]) for c in db.change_log(self.conn, application_id)]


class SaveEmailTest(DbTestCase):
    def test_confirmation_creates_an_application_and_logs_it(self):
        self.save("e1", "confirmation")
        app = self.only_application()
        self.assertEqual((app["company"], app["role"], app["status"]), ("Stripe", "Backend Engineer", "Applied"))
        self.assertEqual(self.changes(app["id"]), [("created", None, "Applied", "tracker")])
        self.assertEqual(db.change_log(self.conn, app["id"])[0]["email_id"], "e1")

    def test_email_body_is_never_stored(self):
        self.save("e1", "confirmation")
        self.conn.close()
        for file in self.path.parent.iterdir():
            self.assertNotIn(b"SECRET EMAIL BODY", file.read_bytes())
        self.conn = db.connect(self.path)

    def test_stored_email_fields_and_link(self):
        self.save("e1", "confirmation")
        saved = db.application_emails(self.conn, self.only_application()["id"])[0]
        self.assertEqual(saved["subject"], "About your application")
        self.assertEqual(saved["sender"], "Stripe <no-reply@greenhouse.io>")
        self.assertEqual(saved["date"], "2026-11-01T09:00:00+00:00")
        self.assertEqual(saved["category"], "confirmation")
        self.assertEqual(saved["link"], "https://gmail/e1")

    def test_forward_move_updates_status_and_logs_the_causing_email(self):
        self.save("e1", "confirmation")
        self.save("e2", "interview")
        app = self.only_application()
        self.assertEqual(app["status"], "Interviewing")
        change = db.change_log(self.conn, app["id"])[-1]
        self.assertEqual((change["field"], change["old_value"], change["new_value"]), ("status", "Applied", "Interviewing"))
        self.assertEqual((change["who"], change["email_id"]), ("tracker", "e2"))

    def test_repeat_is_only_logged(self):
        self.save("e1", "confirmation")
        self.save("e2", "interview")
        self.save("e3", "interview")
        app = self.only_application()
        self.assertEqual(app["status"], "Interviewing")
        self.assertEqual(self.changes(app["id"])[-1], ("repeat", None, "interview", "tracker"))

    def test_duplicate_confirmation_is_ignored_but_stays_on_the_timeline(self):
        self.save("e1", "confirmation")
        self.save("e2", "confirmation")
        app = self.only_application()
        self.assertEqual(len(self.changes(app["id"])), 1)
        self.assertEqual([e["id"] for e in db.application_emails(self.conn, app["id"])], ["e1", "e2"])

    def test_role_is_filled_in_and_logged(self):
        self.save("e1", "confirmation", role=None)
        self.save("e2", "confirmation")
        app = self.only_application()
        self.assertEqual(app["role"], "Backend Engineer")
        self.assertEqual(self.changes(app["id"])[-1], ("role", None, "Backend Engineer", "tracker"))

    def test_info_request_sets_needs_reply_from_the_email_date(self):
        self.save("e1", "confirmation")
        db.save_email(self.conn, email("e2", date="2026-11-03T09:00:00+00:00"), result("info_request"), "link2", NOW)
        app = self.only_application()
        self.assertEqual(app["status"], "Applied")
        self.assertEqual(app["needs_reply_since"], "2026-11-03T09:00:00+00:00")
        self.assertEqual(app["needs_reply_link"], "link2")
        self.assertEqual(self.changes(app["id"])[-1], ("needs_reply", "no", "yes", "tracker"))
        self.assertEqual(db.needs_reply_count(self.conn), 1)

    def test_second_info_request_keeps_the_original_wait(self):
        self.save("e1", "confirmation")
        db.save_email(self.conn, email("e2", date="2026-11-03T09:00:00+00:00"), result("info_request"), "link2", NOW)
        db.save_email(self.conn, email("e3", date="2026-11-04T09:00:00+00:00"), result("info_request"), "link3", NOW)
        self.assertEqual(self.only_application()["needs_reply_since"], "2026-11-03T09:00:00+00:00")

    def test_review_decision_saves_the_reason_and_changes_nothing(self):
        self.save("e1", "interview")
        self.assertEqual(db.applications(self.conn), [])
        waiting = db.review_emails(self.conn)
        self.assertEqual([e["id"] for e in waiting], ["e1"])
        self.assertIn("No application", waiting[0]["review_reason"])

    def test_discarded_email_is_listed_and_changes_nothing(self):
        self.save("e1", "job_alert", company=None, role=None)
        self.assertEqual(db.applications(self.conn), [])
        self.assertEqual(db.review_emails(self.conn), [])
        self.assertEqual([e["id"] for e in db.discarded_emails(self.conn)], ["e1"])

    def test_unusable_model_answer_goes_to_review_as_unclear(self):
        db.save_email(self.conn, email("e1"), None, "link", NOW)
        waiting = db.review_emails(self.conn)[0]
        self.assertEqual(waiting["category"], "unclear")
        self.assertIn("unusable", waiting["review_reason"])

    def test_same_email_is_never_processed_twice(self):
        self.save("e1", "confirmation")
        self.assertTrue(db.is_processed(self.conn, "e1"))
        self.assertFalse(db.is_processed(self.conn, "e2"))
        self.save("e1", "rejection")
        self.assertEqual(self.only_application()["status"], "Applied")

    def test_failure_while_saving_leaves_the_email_unprocessed(self):
        class Boom(Exception):
            pass

        def explode(*args):
            raise Boom

        original = db._log
        db._log = explode
        self.addCleanup(setattr, db, "_log", original)
        with self.assertRaises(Boom):
            self.save("e1", "confirmation")
        db._log = original
        self.assertFalse(db.is_processed(self.conn, "e1"))
        self.assertEqual(db.applications(self.conn), [])

    def test_removed_applications_are_not_matched(self):
        self.save("e1", "confirmation")
        db.set_removed(self.conn, self.only_application()["id"], True, NOW)
        self.save("e2", "rejection")
        self.assertEqual([e["id"] for e in db.review_emails(self.conn)], ["e2"])


class ReviewActionsTest(DbTestCase):
    def setUp(self):
        super().setUp()
        self.save("e1", "confirmation")
        self.app_id = self.only_application()["id"]

    def test_attach_applies_the_status_and_credits_the_person(self):
        self.save("e2", "rejection", company="Hiring System Inc")
        db.attach_email(self.conn, "e2", self.app_id, NOW)
        self.assertEqual(self.only_application()["status"], "Rejected")
        self.assertEqual(db.review_emails(self.conn), [])
        change = db.change_log(self.conn, self.app_id)[-1]
        self.assertEqual((change["field"], change["new_value"], change["who"], change["email_id"]), ("status", "Rejected", "person", "e2"))
        self.assertEqual([e["id"] for e in db.application_emails(self.conn, self.app_id)], ["e1", "e2"])

    def test_attach_info_request_sets_needs_reply(self):
        self.save("e2", "info_request", company=None)
        db.attach_email(self.conn, "e2", self.app_id, NOW)
        self.assertEqual(self.only_application()["needs_reply_link"], "https://gmail/e2")

    def test_attach_unclear_email_only_links_it(self):
        self.save("e2", "unclear")
        db.attach_email(self.conn, "e2", self.app_id, NOW)
        self.assertEqual(self.only_application()["status"], "Applied")
        self.assertEqual(len(db.application_emails(self.conn, self.app_id)), 2)

    def test_attach_can_override_a_final_status(self):
        db.edit_application(self.conn, self.app_id, "Stripe", "Backend Engineer", "Rejected", False, NOW)
        self.save("e2", "interview")
        self.assertEqual(len(db.review_emails(self.conn)), 1)
        db.attach_email(self.conn, "e2", self.app_id, NOW)
        self.assertEqual(self.only_application()["status"], "Interviewing")

    def test_create_uses_the_email_category_and_given_names(self):
        self.save("e2", "rejection", company="Square", role=None)
        new_id = db.create_from_email(self.conn, "e2", "Square, Inc.", "Designer", NOW)
        created = db.application(self.conn, new_id)
        self.assertEqual((created["company"], created["role"], created["status"]), ("Square, Inc.", "Designer", "Rejected"))
        self.assertEqual(self.changes(new_id), [("created", None, "Rejected", "person")])
        self.assertEqual(db.review_emails(self.conn), [])

    def test_create_from_info_request_is_applied_with_needs_reply(self):
        self.save("e2", "info_request", company="Square", role=None)
        created = db.application(self.conn, db.create_from_email(self.conn, "e2", "Square", None, NOW))
        self.assertEqual(created["status"], "Applied")
        self.assertEqual(created["needs_reply_link"], "https://gmail/e2")

    def test_dismiss_moves_the_email_to_discarded(self):
        self.save("e2", "unclear")
        db.dismiss_email(self.conn, "e2")
        self.assertEqual(db.review_emails(self.conn), [])
        self.assertEqual([e["id"] for e in db.discarded_emails(self.conn)], ["e2"])

    def test_discarded_job_alert_can_be_sent_to_review_then_attached(self):
        self.save("e2", "job_alert", company=None, role=None)
        db.send_to_review(self.conn, "e2")
        self.assertEqual(db.discarded_emails(self.conn), [])
        self.assertEqual(db.review_emails(self.conn)[0]["review_reason"], "You sent this to the Review List.")
        db.attach_email(self.conn, "e2", self.app_id, NOW)
        self.assertEqual(self.only_application()["status"], "Applied")

    def test_review_actions_do_nothing_to_emails_not_on_the_review_list(self):
        db.attach_email(self.conn, "e1", self.app_id, NOW)
        db.dismiss_email(self.conn, "e1")
        db.send_to_review(self.conn, "e1")
        self.assertIsNone(db.create_from_email(self.conn, "e1", "Square", None, NOW))
        self.assertEqual(len(db.applications(self.conn)), 1)
        self.assertEqual(db.discarded_emails(self.conn), [])
        self.assertEqual(db.review_emails(self.conn), [])

    def test_attach_to_a_missing_application_does_nothing(self):
        self.save("e2", "unclear")
        db.attach_email(self.conn, "e2", 999, NOW)
        self.assertEqual(len(db.review_emails(self.conn)), 1)

    def test_information_requests_sit_at_the_top_of_the_review_list(self):
        db.save_email(self.conn, email("a"), result("unclear"), "l", NOW)
        db.save_email(self.conn, email("b", date="2026-11-04T00:00:00+00:00"), result("info_request", company=None), "l", NOW)
        self.assertEqual([e["id"] for e in db.review_emails(self.conn)], ["b", "a"])

    def test_top_bar_count_includes_information_requests_on_the_review_list(self):
        self.save("e2", "info_request", company=None)
        self.save("e3", "unclear")
        self.assertEqual(db.needs_reply_count(self.conn), 1)
        self.save("e4", "info_request")
        self.assertEqual(db.needs_reply_count(self.conn), 2)


class PersonEditsTest(DbTestCase):
    def setUp(self):
        super().setUp()
        self.save("e1", "confirmation")
        self.app_id = self.only_application()["id"]

    def test_edit_logs_each_changed_field_as_the_person(self):
        db.edit_application(self.conn, self.app_id, "Stripe Payments", None, "Offer", False, NOW)
        self.assertEqual(
            self.changes(self.app_id)[1:],
            [
                ("company", "Stripe", "Stripe Payments", "person"),
                ("role", "Backend Engineer", None, "person"),
                ("status", "Applied", "Offer", "person"),
            ],
        )

    def test_edit_without_changes_logs_nothing(self):
        db.edit_application(self.conn, self.app_id, "Stripe", "Backend Engineer", "Applied", False, NOW)
        self.assertEqual(len(self.changes(self.app_id)), 1)

    def test_setting_needs_reply_by_hand_starts_the_wait_now_and_is_logged(self):
        db.edit_application(self.conn, self.app_id, "Stripe", "Backend Engineer", "Applied", True, NOW)
        app = self.only_application()
        self.assertEqual(app["needs_reply_since"], NOW)
        self.assertIsNone(app["needs_reply_link"])
        self.assertEqual(self.changes(self.app_id)[-1], ("needs_reply", "no", "yes", "person"))

    def test_mark_reply_done_clears_the_marker_and_is_logged(self):
        self.save("e2", "info_request")
        db.mark_reply_done(self.conn, self.app_id, NOW)
        self.assertIsNone(self.only_application()["needs_reply_since"])
        self.assertEqual(self.changes(self.app_id)[-1], ("needs_reply", "yes", "no", "person"))
        self.assertEqual(db.needs_reply_count(self.conn), 0)

    def test_needs_reply_list_is_oldest_first_and_excludes_removed(self):
        self.save("e2", "confirmation", company="Square", role=None)
        self.save("e3", "confirmation", company="Plaid", role=None)
        db.save_email(self.conn, email("e4", date="2026-11-04T00:00:00+00:00"), result("info_request", role=None), "l", NOW)
        db.save_email(self.conn, email("e5", date="2026-11-02T00:00:00+00:00"), result("info_request", company="Square", role=None), "l", NOW)
        db.save_email(self.conn, email("e6", date="2026-11-01T00:00:00+00:00"), result("info_request", company="Plaid", role=None), "l", NOW)
        plaid_id = next(a["id"] for a in db.applications(self.conn) if a["company"] == "Plaid")
        db.set_removed(self.conn, plaid_id, True, NOW)
        self.assertEqual([a["company"] for a in db.needs_reply_applications(self.conn)], ["Square", "Stripe"])
        self.assertEqual(db.needs_reply_count(self.conn), 2)

    def test_remove_hides_and_restore_brings_back_with_a_log(self):
        db.set_removed(self.conn, self.app_id, True, NOW)
        self.assertEqual(db.applications(self.conn), [])
        self.assertEqual([a["id"] for a in db.applications(self.conn, removed=True)], [self.app_id])
        db.set_removed(self.conn, self.app_id, False, NOW)
        self.assertEqual(self.only_application()["id"], self.app_id)
        self.assertEqual(self.changes(self.app_id)[-2:], [("removed", "no", "yes", "person"), ("removed", "yes", "no", "person")])


if __name__ == "__main__":
    unittest.main()
