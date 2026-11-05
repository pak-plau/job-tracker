import json
import tempfile
import unittest
from pathlib import Path

from evals.compare import rank, score
from evals.label import label_emails


def row(label, predicted, company="Stripe", predicted_company="Stripe", role=None, predicted_role=None, seconds=1.0):
    return {
        "label": {"category": label, "company": company, "role": role},
        "result": {
            "category": predicted,
            "company": predicted_company,
            "role": predicted_role,
            "seconds": seconds,
        },
    }


class ScoreTest(unittest.TestCase):
    def test_counts_each_kind_of_outcome(self):
        rows = [
            row("rejection", "rejection"),           # correct
            row("interview", "rejection"),           # wrong status
            row("irrelevant", "confirmation"),       # wrong status, invented application
            row("rejection", "irrelevant"),          # lost: never shown anywhere
            row("unclear", "irrelevant"),            # lost
            row("interview", "unclear"),             # needless review, but safe
            row("irrelevant", "unclear"),            # needless review, but safe
            row("unclear", "unclear"),               # correct
            {"label": {"category": "offer", "company": None, "role": None}, "result": None},  # error
        ]
        got = score(rows)
        self.assertEqual(got["total"], 9)
        self.assertEqual(got["correct"], 2)
        self.assertEqual(got["wrong"], 2)
        self.assertEqual(got["lost"], 2)
        self.assertEqual(got["needless_review"], 2)
        self.assertEqual(got["errors"], 1)

    def test_real_email_filed_as_job_alert_is_lost(self):
        self.assertEqual(score([row("confirmation", "job_alert")])["lost"], 1)

    def test_company_uses_normalized_names_and_skips_unlabeled(self):
        rows = [
            row("offer", "offer", company="Stripe", predicted_company="Stripe, Inc."),
            row("offer", "offer", company="Stripe", predicted_company="Square"),
            row("offer", "offer", company="Stripe", predicted_company=None),
            row("irrelevant", "irrelevant", company=None, predicted_company="Anything"),
        ]
        got = score(rows)
        self.assertEqual((got["company_correct"], got["company_total"]), (1, 3))

    def test_role_ignores_case_and_skips_unlabeled(self):
        rows = [
            row("offer", "offer", role="Backend Engineer", predicted_role="backend engineer"),
            row("offer", "offer", role="Backend Engineer", predicted_role="Platform Engineer"),
            row("offer", "offer", role=None, predicted_role="Anything"),
        ]
        got = score(rows)
        self.assertEqual((got["role_correct"], got["role_total"]), (1, 2))

    def test_average_seconds_ignores_errors(self):
        rows = [
            row("offer", "offer", seconds=1.0),
            row("offer", "offer", seconds=3.0),
            {"label": {"category": "offer", "company": None, "role": None}, "result": None},
        ]
        self.assertEqual(score(rows)["avg_seconds"], 2.0)

    def test_all_errors_has_no_average(self):
        rows = [{"label": {"category": "offer", "company": None, "role": None}, "result": None}]
        self.assertIsNone(score(rows)["avg_seconds"])


class RankTest(unittest.TestCase):
    def scored(self, wrong=0, lost=0, errors=0, needless_review=0):
        return {"wrong": wrong, "lost": lost, "errors": errors, "needless_review": needless_review}

    def test_fewest_harmful_mistakes_wins(self):
        results = {"small": self.scored(wrong=2), "big": self.scored(wrong=1)}
        self.assertEqual(rank(results), ["big", "small"])

    def test_lost_emails_and_errors_count_as_harmful(self):
        results = {"a": self.scored(lost=1), "b": self.scored(errors=2), "c": self.scored()}
        self.assertEqual(rank(results), ["c", "a", "b"])

    def test_marking_everything_unclear_does_not_beat_a_real_answer(self):
        results = {"lazy": self.scored(needless_review=30), "useful": self.scored(needless_review=2)}
        self.assertEqual(rank(results), ["useful", "lazy"])

    def test_full_tie_goes_to_the_smaller_model_listed_first(self):
        results = {"smaller": self.scored(), "bigger": self.scored()}
        self.assertEqual(rank(results), ["smaller", "bigger"])


class LabelEmailsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "test_emails.jsonl"

    def email(self, id):
        return {"id": id, "sender": "a@b.c", "subject": "S", "date": "2026-01-01T00:00:00+00:00", "text": "T"}

    def saved(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def run_with(self, ids, answers):
        replies = iter(answers)
        label_emails(
            ids,
            lambda id: self.email(id),
            self.path,
            ask=lambda prompt: next(replies),
            show=lambda text: None,
        )

    def test_saves_category_company_and_role(self):
        self.run_with(["a"], ["r", "Stripe", "Backend Engineer"])
        saved = self.saved()[0]
        self.assertEqual(saved["id"], "a")
        self.assertEqual(saved["text"], "T")
        self.assertEqual(saved["label"], {"category": "rejection", "company": "Stripe", "role": "Backend Engineer"})

    def test_blank_company_and_role_are_saved_as_none(self):
        self.run_with(["a"], ["i", "", ""])
        self.assertEqual(self.saved()[0]["label"], {"category": "interview", "company": None, "role": None})

    def test_irrelevant_emails_do_not_ask_for_company_or_role(self):
        self.run_with(["a", "b"], ["n", "o", "Acme", ""])
        self.assertEqual(self.saved()[0]["label"], {"category": "irrelevant", "company": None, "role": None})
        self.assertEqual(self.saved()[1]["label"], {"category": "offer", "company": "Acme", "role": None})

    def test_job_alerts_do_not_ask_for_company_or_role(self):
        self.run_with(["a", "b"], ["j", "o", "Acme", ""])
        self.assertEqual(self.saved()[0]["label"], {"category": "job_alert", "company": None, "role": None})
        self.assertEqual(self.saved()[1]["label"], {"category": "offer", "company": "Acme", "role": None})

    def test_information_requests_can_be_labeled_with_company_and_role(self):
        self.run_with(["a"], ["m", "Stripe", ""])
        self.assertEqual(self.saved()[0]["label"], {"category": "info_request", "company": "Stripe", "role": None})

    def test_shows_all_the_text_the_model_will_see(self):
        shown = []
        label_emails(
            ["a"],
            lambda id: {**self.email(id), "text": "x" * 2000 + "THE DECISION"},
            self.path,
            ask=lambda prompt: "s",
            show=shown.append,
        )
        self.assertIn("THE DECISION", shown[0])

    def test_unrecognized_answer_asks_again(self):
        self.run_with(["a"], ["zzz", "c", "Acme", ""])
        self.assertEqual(self.saved()[0]["label"]["category"], "confirmation")

    def test_skip_saves_nothing_and_moves_on(self):
        self.run_with(["a", "b"], ["s", "n"])
        self.assertEqual([e["id"] for e in self.saved()], ["b"])

    def test_quit_keeps_what_was_labeled(self):
        self.run_with(["a", "b"], ["n", "q"])
        self.assertEqual([e["id"] for e in self.saved()], ["a"])

    def test_rerun_skips_emails_already_labeled_without_fetching_them(self):
        self.run_with(["a"], ["n"])
        fetched = []
        replies = iter(["n"])
        label_emails(
            ["a", "b"],
            lambda id: fetched.append(id) or self.email(id),
            self.path,
            ask=lambda prompt: next(replies),
            show=lambda text: None,
        )
        self.assertEqual(fetched, ["b"])
        self.assertEqual([e["id"] for e in self.saved()], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
