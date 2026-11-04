"""Read-only access to the person's Gmail. See docs/adr/0001-local-only.md."""
import base64
from datetime import datetime, timezone
from email import message_from_bytes, policy
from html.parser import HTMLParser
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CREDENTIALS_FILE = DATA_DIR / "credentials.json"
TOKEN_FILE = DATA_DIR / "token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# shortcut: only the start of an email reaches the model, raise this if the test shows decisions being cut off.
MAX_TEXT_CHARS = 8000

# Deliberately broad: the local model is free, and a narrow search could miss a rejection unnoticed.
SEARCH = (
    '("thank you for applying" OR "thanks for applying" OR "your application" '
    'OR "application received" OR "received your application" OR "interview" '
    'OR "next steps" OR "unfortunately" OR "other candidates" OR "move forward" '
    'OR "moving forward" OR "offer letter" OR "job offer" '
    "OR from:greenhouse.io OR from:greenhouse-mail.io OR from:lever.co "
    "OR from:myworkday.com OR from:ashbyhq.com OR from:smartrecruiters.com "
    "OR from:icims.com OR from:jobvite.com OR from:workablemail.com)"
)


def connect():
    """Return a Gmail client, opening a browser sign-in if there is no usable saved sign-in."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = None  # revoked or expired: fall through to a new sign-in
    if not creds or not creds.valid:
        if not CREDENTIALS_FILE.exists():
            raise SystemExit(f"Missing {CREDENTIALS_FILE}. Run scripts/setup-google.sh first.")
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def search(service, query):
    """Yield the id of every message matching a Gmail search, newest first."""
    page_token = None
    while True:
        response = service.users().messages().list(userId="me", q=query, pageToken=page_token).execute()
        for message in response.get("messages", []):
            yield message["id"]
        page_token = response.get("nextPageToken")
        if not page_token:
            return


def fetch(service, message_id):
    return parse_message(service.users().messages().get(userId="me", id=message_id, format="raw").execute())


def parse_message(response):
    """Turn a Gmail format=raw response into the fields the tracker uses."""
    raw = response["raw"]
    message = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default)
    sent = datetime.fromtimestamp(int(response["internalDate"]) / 1000, timezone.utc)
    return {
        "id": response["id"],
        "sender": str(message["From"] or ""),
        "subject": str(message["Subject"] or ""),
        "date": sent.isoformat(),
        "text": _body_text(message)[:MAX_TEXT_CHARS],
    }


def _body_text(message):
    plain = message.get_body(preferencelist=("plain",))
    text = _decode(plain).strip() if plain else ""
    if text:
        return text
    html = message.get_body(preferencelist=("html",))
    return _html_to_text(_decode(html)) if html else ""


def _decode(part):
    try:
        return part.get_content()
    except (LookupError, UnicodeError):  # senders declare charsets Python doesn't know
        return part.get_payload(decode=True).decode("utf-8", "replace")


def _html_to_text(html):
    parser = _TextExtractor()
    parser.feed(html)
    return " ".join(" ".join(parser.chunks).split())


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skipping += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skipping:
            self.skipping -= 1

    def handle_data(self, data):
        if not self.skipping:
            self.chunks.append(data)
