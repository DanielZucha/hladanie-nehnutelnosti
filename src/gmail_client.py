"""Gmail API client for reading alert emails and sending reports."""

import base64
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_gmail_service(
    credentials_file: str = "credentials.json",
    token_file: str = "token.json",
):
    """Authenticate and return Gmail API service."""
    creds = None
    token_path = Path(token_file)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)

        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def fetch_unread_alerts(
    service,
    sender_addresses: list[str],
    max_results: int = 50,
) -> list[dict]:
    """Fetch unread emails from specified senders.

    Returns list of dicts with: message_id, sender, subject, html_body, date.
    """
    # Build OR query for senders
    sender_query = " OR ".join(f"from:{addr}" for addr in sender_addresses)
    query = f"is:unread ({sender_query})"

    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )

    messages = results.get("messages", [])
    if not messages:
        logger.info("No unread alert emails found")
        return []

    parsed = []
    for msg_meta in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_meta["id"], format="full")
            .execute()
        )

        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        html_body = _extract_html_body(msg["payload"])

        parsed.append({
            "message_id": msg_meta["id"],
            "sender": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "html_body": html_body or "",
            "date": headers.get("Date", ""),
        })

    logger.info("Fetched %d unread alert emails", len(parsed))
    return parsed


def mark_as_read(service, message_id: str) -> None:
    """Mark a message as read by removing UNREAD label."""
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def send_html_email(
    service,
    to: str,
    subject: str,
    html_body: str,
    inline_images: Optional[dict[str, bytes]] = None,
) -> None:
    """Send an HTML email, optionally with inline images.

    inline_images: dict mapping Content-ID to PNG bytes.
    """
    msg = MIMEMultipart("related")
    msg["To"] = to
    msg["Subject"] = subject

    html_part = MIMEText(html_body, "html", "utf-8")
    msg.attach(html_part)

    if inline_images:
        for cid, img_bytes in inline_images.items():
            img = MIMEImage(img_bytes, "png")
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
            msg.attach(img)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()

    logger.info("Report email sent to %s", to)


def _extract_html_body(payload: dict) -> Optional[str]:
    """Recursively extract HTML body from Gmail message payload."""
    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        result = _extract_html_body(part)
        if result:
            return result

    return None
