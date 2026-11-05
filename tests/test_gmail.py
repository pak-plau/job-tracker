import base64
import unittest
from email.message import EmailMessage

from job_tracker.gmail import MAX_TEXT_CHARS, parse_message


def gmail_response(message):
    raw = message if isinstance(message, bytes) else message.as_bytes()
    return {
        "id": "abc123",
        "threadId": "thread1",
        "labelIds": ["INBOX"],
        "snippet": "ignored",
        "sizeEstimate": len(raw),
        "historyId": "1",
        "internalDate": "1767225600000",  # 2026-01-01T00:00:00Z
        "raw": base64.urlsafe_b64encode(raw).decode().rstrip("="),
    }


def message(plain=None, html=None):
    msg = EmailMessage()
    msg["From"] = "Stripe Recruiting <no-reply@greenhouse.io>"
    msg["Subject"] = "Your application to Stripe"
    if plain is not None:
        msg.set_content(plain)
    if html is not None:
        if plain is None:
            msg.set_content(html, subtype="html")
        else:
            msg.add_alternative(html, subtype="html")
    return msg


class ParseMessageTest(unittest.TestCase):
    def test_reads_headers_and_gmail_fields(self):
        parsed = parse_message(gmail_response(message(plain="Hello")))
        self.assertEqual(parsed["id"], "abc123")
        self.assertEqual(parsed["sender"], "Stripe Recruiting <no-reply@greenhouse.io>")
        self.assertEqual(parsed["subject"], "Your application to Stripe")
        self.assertEqual(parsed["date"], "2026-01-01T00:00:00+00:00")

    def test_prefers_plain_text_over_html(self):
        parsed = parse_message(gmail_response(message(plain="Plain words", html="<p>Html words</p>")))
        self.assertEqual(parsed["text"], "Plain words")

    def test_html_only_email_becomes_text_without_markup_or_scripts(self):
        html = "<style>p {color: red}</style><p>We regret &amp; decline</p><script>steal()</script>"
        parsed = parse_message(gmail_response(message(html=html)))
        self.assertEqual(parsed["text"], "We regret & decline")

    def test_blank_plain_part_falls_back_to_html(self):
        parsed = parse_message(gmail_response(message(plain="   \n", html="<p>Real content</p>")))
        self.assertEqual(parsed["text"], "Real content")

    def test_html_with_more_words_beats_plain_missing_the_decision(self):
        html = "<p>Your application was sent. Unfortunately we will not move forward.</p>"
        parsed = parse_message(gmail_response(message(plain="Your application was sent.", html=html)))
        self.assertIn("Unfortunately", parsed["text"])

    def test_links_are_removed_from_plain_text(self):
        plain = "Your application was viewed\nhttps://example.com/track?id=1\nView job: http://example.com/j/2\nThanks"
        parsed = parse_message(gmail_response(message(plain=plain)))
        self.assertNotIn("example.com", parsed["text"])
        self.assertIn("Your application was viewed", parsed["text"])
        self.assertIn("Thanks", parsed["text"])

    def test_invisible_filler_is_removed_from_html(self):
        parsed = parse_message(gmail_response(message(html="<p>We regret</p><div>͏ ͏ ͏</div>")))
        self.assertEqual(parsed["text"], "We regret")

    def test_invisible_filler_does_not_count_as_html_words(self):
        html = "<p>Html words</p><div>͏ ͏ ͏ ͏</div>"
        parsed = parse_message(gmail_response(message(plain="Plain words", html=html)))
        self.assertEqual(parsed["text"], "Plain words")

    def test_decodes_declared_charset(self):
        raw = (
            b"From: a@b.c\r\nSubject: Hi\r\n"
            b"Content-Type: text/plain; charset=iso-8859-1\r\n\r\nM\xfcller GmbH"
        )
        self.assertEqual(parse_message(gmail_response(raw))["text"], "Müller GmbH")

    def test_unknown_charset_still_returns_text(self):
        raw = (
            b"From: a@b.c\r\nSubject: Hi\r\n"
            b"Content-Type: text/plain; charset=not-a-charset\r\n\r\nStill readable"
        )
        self.assertEqual(parse_message(gmail_response(raw))["text"], "Still readable")

    def test_email_with_no_readable_body_has_empty_text(self):
        msg = EmailMessage()
        msg["From"] = "a@b.c"
        msg["Subject"] = "Attachment only"
        msg.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="offer.pdf")
        self.assertEqual(parse_message(gmail_response(msg))["text"], "")

    def test_long_body_is_capped(self):
        parsed = parse_message(gmail_response(message(plain="x" * (MAX_TEXT_CHARS + 500))))
        self.assertEqual(len(parsed["text"]), MAX_TEXT_CHARS)


if __name__ == "__main__":
    unittest.main()
