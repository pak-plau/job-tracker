import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from job_tracker.classify import ClassifyError, classify, normalize_company

EMAIL = {
    "id": "abc123",
    "sender": "no-reply@greenhouse.io",
    "subject": "Your application",
    "date": "2026-01-01T00:00:00+00:00",
    "text": "Unfortunately we will not be moving forward.",
}


def ollama_reply(content, status=200):
    """A real local HTTP server standing in for Ollama, recording what it was sent."""
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = json.dumps({
                "model": "m",
                "message": {"role": "assistant", "content": content},
                "done": True,
                "total_duration": 3_000_000_000,
                "load_duration": 1_000_000_000,
                "eval_count": 20,
            }).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, received


class ClassifyTest(unittest.TestCase):
    def call(self, content, status=200, **kwargs):
        server, received = ollama_reply(content, status)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        return classify(EMAIL, "granite4.2:3b", url=url, **kwargs), received

    def test_returns_the_models_answer_with_seconds_excluding_load_time(self):
        reply = json.dumps({"category": "rejection", "company": "Stripe", "role": "Backend Engineer"})
        result, _ = self.call(reply)
        self.assertEqual(result, {
            "category": "rejection",
            "company": "Stripe",
            "role": "Backend Engineer",
            "seconds": 2.0,
        })

    def test_request_is_deterministic_structured_and_without_thinking(self):
        reply = json.dumps({"category": "offer", "company": None, "role": None})
        _, received = self.call(reply)
        body = received["body"]
        self.assertEqual(received["path"], "/api/chat")
        self.assertEqual(body["model"], "granite4.2:3b")
        self.assertIs(body["stream"], False)
        self.assertIs(body["think"], False)
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertIn("num_predict", body["options"])
        self.assertEqual(
            sorted(body["format"]["properties"]["category"]["enum"]),
            ["confirmation", "info_request", "interview", "irrelevant", "offer", "rejection", "unclear"],
        )
        self.assertNotIn("num_gpu", body["options"])

    def test_email_content_reaches_the_model(self):
        reply = json.dumps({"category": "offer", "company": None, "role": None})
        _, received = self.call(reply)
        sent = json.dumps(received["body"]["messages"])
        self.assertIn("Unfortunately we will not be moving forward.", sent)
        self.assertIn("Your application", sent)
        self.assertIn("no-reply@greenhouse.io", sent)

    def test_cpu_mode_keeps_the_model_off_the_gpu(self):
        reply = json.dumps({"category": "offer", "company": None, "role": None})
        _, received = self.call(reply, cpu=True)
        self.assertEqual(received["body"]["options"]["num_gpu"], 0)

    def test_blank_company_and_role_become_none(self):
        reply = json.dumps({"category": "rejection", "company": "  ", "role": ""})
        result, _ = self.call(reply)
        self.assertIsNone(result["company"])
        self.assertIsNone(result["role"])

    def test_non_json_answer_is_an_error(self):
        with self.assertRaises(ClassifyError):
            self.call("not json")

    def test_category_outside_the_allowed_list_is_an_error(self):
        with self.assertRaises(ClassifyError):
            self.call(json.dumps({"category": "hired", "company": None, "role": None}))

    def test_answer_missing_a_field_is_an_error(self):
        with self.assertRaises(ClassifyError):
            self.call(json.dumps({"category": "offer"}))

    def test_http_error_names_the_model(self):
        with self.assertRaisesRegex(ClassifyError, "granite4.2:3b"):
            self.call("{}", status=404)

    def test_unreachable_ollama_says_so(self):
        with self.assertRaisesRegex(ClassifyError, "Ollama"):
            classify(EMAIL, "m", url="http://127.0.0.1:9")


class NormalizeCompanyTest(unittest.TestCase):
    def test_table(self):
        for name, want in [
            ("Stripe, Inc.", "stripe"),
            ("STRIPE", "stripe"),
            ("  Stripe  ", "stripe"),
            ("Acme Co., Ltd.", "acme"),
            ("Müller GmbH", "müller"),
            ("Stripe Payments", "stripe payments"),
            ("AT&T", "at&t"),
            ("Inc", "inc"),
            ("", ""),
            (None, ""),
        ]:
            with self.subTest(name=name):
                self.assertEqual(normalize_company(name), want)


if __name__ == "__main__":
    unittest.main()
