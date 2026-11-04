"""Compare local models on the emails labeled with evals/label.py.

Run: python -m evals.compare [--cpu] [--models a b] [--emails path]
"""
import argparse
import json
import sys
from pathlib import Path

from job_tracker.classify import ClassifyError, classify, normalize_company
from job_tracker.gmail import DATA_DIR

# Smallest download first, because a full tie goes to the model listed first.
MODELS = ["gemma3:1b", "granite4.2:3b", "qwen3.5:2b", "gemma3:4b"]


def score(rows):
    """Count outcomes for one model, where each row is {label, result}, with result None on an error."""
    got = {
        "total": len(rows), "correct": 0, "wrong": 0, "lost": 0, "needless_review": 0, "errors": 0,
        "company_correct": 0, "company_total": 0, "role_correct": 0, "role_total": 0,
    }
    seconds = []
    for row in rows:
        label, result = row["label"], row["result"]
        if result is None:
            got["errors"] += 1
            continue
        seconds.append(result["seconds"])
        got[_outcome(label["category"], result["category"])] += 1
        if label["company"]:
            got["company_total"] += 1
            got["company_correct"] += normalize_company(label["company"]) == normalize_company(result["company"])
        if label["role"]:
            got["role_total"] += 1
            got["role_correct"] += _same_text(label["role"], result["role"])
    got["avg_seconds"] = sum(seconds) / len(seconds) if seconds else None
    return got


def _outcome(label, predicted):
    if predicted == label:
        return "correct"
    if predicted == "irrelevant":
        return "lost"  # the email would be discarded rather than acted on or reviewed
    if predicted == "unclear":
        return "needless_review"
    return "wrong"


def _same_text(a, b):
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def rank(results):
    """Best model first: fewest harmful mistakes, then fewest needless reviews, then listed order."""
    # Without the review tiebreak, a model answering "unclear" to everything would win with no mistakes.
    return sorted(
        results,
        key=lambda model: (
            results[model]["wrong"] + results[model]["lost"] + results[model]["errors"],
            results[model]["needless_review"],
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=MODELS)
    parser.add_argument("--cpu", action="store_true", help="keep models off the GPU, to see speed without one")
    parser.add_argument("--emails", type=Path, default=DATA_DIR / "test_emails.jsonl")
    args = parser.parse_args()

    sys.stdout.reconfigure(errors="replace")
    emails = [json.loads(line) for line in args.emails.read_text(encoding="utf-8").splitlines()]
    results, details = {}, {}
    for model in args.models:
        print(f"Testing {model} on {len(emails)} emails...", flush=True)
        rows = []
        for email in emails:
            try:
                result = classify(email, model, cpu=args.cpu)
            except ClassifyError as error:
                print(f"  ERROR on {email['subject']!r}: {error}", flush=True)
                result = None
            rows.append({"subject": email["subject"], "label": email["label"], "result": result})
        results[model], details[model] = score(rows), rows

    for model in rank(results):
        got = results[model]
        speed = f"{got['avg_seconds']:.1f}s/email" if got["avg_seconds"] is not None else "no speed data"
        print(
            f"\n{model}: {got['correct']}/{got['total']} correct, {got['wrong']} wrong status, "
            f"{got['lost']} lost, {got['needless_review']} needless review, {got['errors']} errors, {speed}\n"
            f"  company {got['company_correct']}/{got['company_total']}, role {got['role_correct']}/{got['role_total']}"
        )
        for row in details[model]:
            predicted = row["result"]["category"] if row["result"] else "error"
            if predicted != row["label"]["category"]:
                print(f"  - {row['subject']!r}: labeled {row['label']['category']}, got {predicted}")

    out = args.emails.with_name("model_results.json")
    out.write_text(json.dumps({"scores": results, "details": details}, indent=2), encoding="utf-8")
    print(f"\nBest: {rank(results)[0]}. Details saved to {out}")


if __name__ == "__main__":
    main()
