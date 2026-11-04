"""Label real emails from your inbox as test data for evals/compare.py.

Run: python -m evals.label [--days 90] [--query "offer letter"]
Labeled emails, including their text, stay in the git-ignored data/ folder.
"""
import argparse
import json
import sys
from collections import Counter

from job_tracker.gmail import DATA_DIR, SEARCH, connect, fetch, search

TEST_EMAILS_FILE = DATA_DIR / "test_emails.jsonl"
ANSWERS = {
    "c": "confirmation",
    "i": "interview",
    "m": "info_request",
    "r": "rejection",
    "o": "offer",
    "u": "unclear",
    "n": "irrelevant",
}
PROMPT = "[c]onfirmation [i]nterview [m]ore info requested [r]ejection [o]ffer [u]nclear [n]ot relevant | [s]kip [q]uit: "


def label_emails(ids, fetch_email, path, ask=input, show=print):
    """Ask for a label for each new email, saving after every answer so quitting loses nothing."""
    saved = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    done = {email["id"] for email in saved}
    counts = Counter(email["label"]["category"] for email in saved)

    for message_id in ids:
        if message_id in done:
            continue
        email = fetch_email(message_id)
        show(_describe(email, counts))
        answer = ""
        while answer not in ANSWERS and answer not in ("s", "q"):
            answer = ask(PROMPT).strip().lower()
        if answer == "q":
            return
        if answer == "s":
            continue
        category = ANSWERS[answer]
        company = role = None
        if category != "irrelevant":
            company = ask("Company (Enter if not stated): ").strip() or None
            role = ask("Role (Enter if not stated): ").strip() or None
        email["label"] = {"category": category, "company": company, "role": role}
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(email) + "\n")
        counts[category] += 1


def _describe(email, counts):
    so_far = ", ".join(f"{category}: {counts[category]}" for category in ANSWERS.values())
    text = f"\n--- Labeled so far: {so_far}\nFrom: {email['sender']}\nSubject: {email['subject']}\n\n{email['text']}\n"
    # Emails can contain terminal control characters, so only printable text is displayed.
    return "".join(c if c.isprintable() or c == "\n" else " " for c in text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--query", default=SEARCH, help="Gmail search, for hunting rarer emails like offers")
    args = parser.parse_args()

    sys.stdout.reconfigure(errors="replace")
    DATA_DIR.mkdir(exist_ok=True)
    service = connect()
    ids = search(service, f"{args.query} newer_than:{args.days}d")
    label_emails(ids, lambda message_id: fetch(service, message_id), TEST_EMAILS_FILE)
    print(f"\nSaved to {TEST_EMAILS_FILE}")


if __name__ == "__main__":
    main()
