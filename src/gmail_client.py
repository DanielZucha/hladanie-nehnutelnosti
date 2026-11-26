"""Email client using IMAP (read) and SMTP (send) with Gmail App Passwords.

No Google Cloud Console needed. Just:
1. Enable 2FA on the Gmail account
2. Generate an App Password (Google Account > Security > App Passwords)
3. Set EMAIL_ADDRESS and EMAIL_APP_PASSWORD in .env or config
"""

import email
import imaplib
import logging
import smtplib
from email.header import decode_header, make_header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
PROCESSED_LABEL = "processed"  # Gmail label set after a successful merge


def _connect(email_address: str, app_password: str) -> imaplib.IMAP4_SSL:
    """Open an IMAP session on the inbox (read-write, so labels can be added)."""
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(email_address, app_password)
    conn.select("INBOX")
    return conn


def fetch_new_alerts(
    email_address: str,
    app_password: str,
    sender_addresses: list[str],
    max_results: int = 50,
    since_days: int = 7,
) -> list[dict]:
    """Fetch alerts from the senders that lack the processed label, newer than since_days.

    The label, not the read flag, tracks what the pipeline has seen, so a human may open
    alerts freely. BODY.PEEK leaves the read flag as it was. The age bound stops a backlog
    of stale alerts (e.g. after downtime) from flooding one run.

    Returns list of dicts with: uid, sender, subject, html_body, date.
    """
    conn = _connect(email_address, app_password)
    all_messages = []
    try:
        for sender in sender_addresses:
            query = f"from:{sender} -label:{PROCESSED_LABEL} newer_than:{since_days}d"
            status, data = conn.uid("SEARCH", "X-GM-RAW", f'"{query}"')
            if status != "OK" or not data[0]:
                continue
            for uid in data[0].split()[-max_results:]:  # newest first when capped
                status, msg_data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                all_messages.append({
                    "uid": uid.decode(),
                    "sender": msg.get("From", ""),
                    "subject": str(make_header(decode_header(msg.get("Subject", "")))),
                    "html_body": _extract_html_body(msg) or "",
                    "date": msg.get("Date", ""),
                })
    finally:
        conn.logout()

    logger.info("Fetched %d new alert emails", len(all_messages))
    return all_messages


def label_processed(email_address: str, app_password: str, uids: list[str]) -> None:
    """Add the processed label to the given UIDs in one IMAP session."""
    if not uids:
        return
    conn = _connect(email_address, app_password)
    try:
        conn.create(PROCESSED_LABEL)  # NO when it exists already; harmless
        for uid in uids:
            status, _ = conn.uid("STORE", uid, "+X-GM-LABELS", f"({PROCESSED_LABEL})")
            if status != "OK":  # imaplib returns NO instead of raising; unlabelled alerts get re-fetched
                logger.warning("Label failed for uid %s", uid)
    finally:
        conn.logout()


def send_html_email(
    email_address: str,
    app_password: str,
    to: list[str],
    subject: str,
    html_body: str,
    inline_images: Optional[dict[str, bytes]] = None,
) -> None:
    """Send an HTML email via SMTP, optionally with inline images.

    to: list of recipient email addresses.
    inline_images: dict mapping Content-ID to PNG bytes.
    """
    msg = MIMEMultipart("related")
    msg["From"] = email_address
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject

    html_part = MIMEText(html_body, "html", "utf-8")
    msg.attach(html_part)

    if inline_images:
        for cid, img_bytes in inline_images.items():
            img = MIMEImage(img_bytes, "png")
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
            msg.attach(img)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(email_address, app_password)
        server.send_message(msg)

    logger.info("Report email sent to %s", ", ".join(to))


def _extract_html_body(msg: email.message.Message) -> Optional[str]:
    """Extract HTML body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        if msg.get_content_type() == "text/html":
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")

    return None
