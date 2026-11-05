import unittest

from job_tracker.rules import decide


def app(id=1, company="Stripe", role="Backend Engineer", status="Applied"):
    return {"id": id, "company": company, "role": role, "status": status}


class DiscardAndUnclearTest(unittest.TestCase):
    def test_job_alerts_and_irrelevant_emails_are_discarded_even_with_a_matching_company(self):
        for category in ("job_alert", "irrelevant"):
            self.assertEqual(decide(category, "Stripe", None, [app()]), {"action": "discard"})

    def test_unclear_emails_go_to_review_even_with_a_match(self):
        self.assertEqual(decide("unclear", "Stripe", None, [app()])["action"], "review")

    def test_no_company_goes_to_review(self):
        for category in ("confirmation", "interview", "info_request", "rejection", "offer"):
            for company in (None, "", " , "):
                self.assertEqual(decide(category, company, "Backend Engineer", [app()])["action"], "review")


class ForwardMovesTest(unittest.TestCase):
    def test_moves_status_forward_on_a_single_match(self):
        cases = [
            ("interview", "Applied", "Interviewing"),
            ("rejection", "Applied", "Rejected"),
            ("rejection", "Interviewing", "Rejected"),
            ("offer", "Applied", "Offer"),
            ("offer", "Interviewing", "Offer"),
        ]
        for category, before, after in cases:
            got = decide(category, "Stripe", "Backend Engineer", [app(status=before)])
            self.assertEqual((got["action"], got["application_id"], got["status"]), ("update", 1, after))

    def test_repeat_interview_is_only_logged(self):
        got = decide("interview", "Stripe", "Backend Engineer", [app(status="Interviewing")])
        self.assertEqual((got["action"], got["application_id"]), ("log", 1))


class FinalStatusTest(unittest.TestCase):
    def test_repeat_of_a_final_status_is_only_logged(self):
        for category, status in (("rejection", "Rejected"), ("offer", "Offer")):
            got = decide(category, "Stripe", "Backend Engineer", [app(status=status)])
            self.assertEqual((got["action"], got["application_id"]), ("log", 1))

    def test_contradicting_a_final_status_goes_to_review(self):
        cases = [
            ("interview", "Rejected"),
            ("interview", "Offer"),
            ("offer", "Rejected"),
            ("rejection", "Offer"),
            ("info_request", "Rejected"),
            ("info_request", "Offer"),
        ]
        for category, status in cases:
            got = decide(category, "Stripe", "Backend Engineer", [app(status=status)])
            self.assertEqual(got["action"], "review", (category, status))
            self.assertIn(status, got["reason"])

    def test_open_application_is_preferred_over_a_final_one(self):
        apps = [app(id=1, role=None, status="Rejected"), app(id=2, role=None, status="Applied")]
        got = decide("interview", "Stripe", None, apps)
        self.assertEqual((got["action"], got["application_id"]), ("update", 2))


class InformationRequestTest(unittest.TestCase):
    def test_sets_needs_reply_without_changing_status(self):
        for status in ("Applied", "Interviewing"):
            got = decide("info_request", "Stripe", "Backend Engineer", [app(status=status)])
            self.assertEqual(got["action"], "update")
            self.assertIsNone(got["status"])
            self.assertIs(got["needs_reply"], True)

    def test_status_moves_do_not_set_needs_reply(self):
        self.assertIs(decide("interview", "Stripe", None, [app()])["needs_reply"], False)


class MatchingTest(unittest.TestCase):
    def test_company_names_are_compared_after_normalizing(self):
        got = decide("rejection", "stripe, Inc.", None, [app()])
        self.assertEqual(got["application_id"], 1)

    def test_other_companies_do_not_match(self):
        got = decide("rejection", "Square", None, [app()])
        self.assertEqual(got["action"], "review")

    def test_named_role_ignores_case_and_picks_the_matching_application(self):
        apps = [app(id=1, role="Backend Engineer"), app(id=2, role="Data Engineer")]
        got = decide("rejection", "Stripe", " data engineer ", apps)
        self.assertEqual(got["application_id"], 2)

    def test_named_role_with_no_matching_application_goes_to_review(self):
        got = decide("rejection", "Stripe", "Data Engineer", [app(role="Backend Engineer")])
        self.assertEqual(got["action"], "review")

    def test_no_role_matches_the_only_open_application(self):
        apps = [app(id=1, status="Rejected"), app(id=2, role="Data Engineer", status="Applied")]
        self.assertEqual(decide("rejection", "Stripe", None, apps)["application_id"], 2)

    def test_no_role_with_several_open_applications_goes_to_review(self):
        apps = [app(id=1, role="Backend Engineer"), app(id=2, role="Data Engineer")]
        self.assertEqual(decide("rejection", "Stripe", None, apps)["action"], "review")

    def test_same_role_twice_goes_to_review(self):
        apps = [app(id=1), app(id=2)]
        self.assertEqual(decide("rejection", "Stripe", "Backend Engineer", apps)["action"], "review")

    def test_several_final_applications_go_to_review(self):
        apps = [app(id=1, status="Rejected"), app(id=2, role="Data Engineer", status="Rejected")]
        self.assertEqual(decide("rejection", "Stripe", None, apps)["action"], "review")


class RoleFillTest(unittest.TestCase):
    def test_named_role_fills_in_an_application_with_an_unknown_role(self):
        got = decide("interview", "Stripe", "Backend Engineer", [app(role=None)])
        self.assertEqual((got["action"], got["application_id"], got["role"]), ("update", 1, "Backend Engineer"))

    def test_exact_role_beats_an_application_with_an_unknown_role(self):
        apps = [app(id=1, role=None), app(id=2, role="Backend Engineer")]
        got = decide("interview", "Stripe", "Backend Engineer", apps)
        self.assertEqual(got["application_id"], 2)
        self.assertIsNone(got["role"])

    def test_several_applications_with_unknown_roles_go_to_review(self):
        apps = [app(id=1, role=None), app(id=2, role=None)]
        self.assertEqual(decide("interview", "Stripe", "Backend Engineer", apps)["action"], "review")

    def test_known_role_is_never_overwritten(self):
        self.assertIsNone(decide("interview", "Stripe", None, [app()])["role"])


class ConfirmationTest(unittest.TestCase):
    def test_new_company_creates_an_application(self):
        got = decide("confirmation", "Stripe", "Backend Engineer", [])
        self.assertEqual(got, {"action": "create", "company": "Stripe", "role": "Backend Engineer"})

    def test_role_may_be_unknown_when_creating(self):
        self.assertEqual(decide("confirmation", "Stripe", None, [])["role"], None)

    def test_duplicate_on_an_open_application_is_ignored(self):
        for status in ("Applied", "Interviewing"):
            got = decide("confirmation", "Stripe", "Backend Engineer", [app(status=status)])
            self.assertEqual((got["action"], got["application_id"]), ("ignore", 1))

    def test_duplicate_with_no_role_on_the_only_open_application_is_ignored(self):
        self.assertEqual(decide("confirmation", "Stripe", None, [app()])["action"], "ignore")

    def test_duplicate_can_fill_in_an_unknown_role(self):
        got = decide("confirmation", "Stripe", "Backend Engineer", [app(role=None)])
        self.assertEqual((got["action"], got["role"]), ("ignore", "Backend Engineer"))

    def test_different_role_at_the_same_company_creates_an_application(self):
        got = decide("confirmation", "Stripe", "Data Engineer", [app(role="Backend Engineer")])
        self.assertEqual(got["action"], "create")

    def test_no_role_with_several_open_applications_goes_to_review(self):
        apps = [app(id=1, role="Backend Engineer"), app(id=2, role="Data Engineer")]
        self.assertEqual(decide("confirmation", "Stripe", None, apps)["action"], "review")

    def test_matching_only_a_rejected_application_creates_a_new_one(self):
        got = decide("confirmation", "Stripe", "Backend Engineer", [app(status="Rejected")])
        self.assertEqual(got["action"], "create")

    def test_no_role_matching_only_rejected_applications_creates_a_new_one(self):
        apps = [app(id=1, status="Rejected"), app(id=2, role="Data Engineer", status="Rejected")]
        self.assertEqual(decide("confirmation", "Stripe", None, apps)["action"], "create")

    def test_matching_an_offer_goes_to_review(self):
        got = decide("confirmation", "Stripe", "Backend Engineer", [app(status="Offer")])
        self.assertEqual(got["action"], "review")


if __name__ == "__main__":
    unittest.main()
