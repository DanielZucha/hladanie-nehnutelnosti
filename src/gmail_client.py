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
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def fetch_unread_alerts(
    email_address: str,
    app_password: str,
    sender_addresses: list[str],
    max_results: int = 50,
) -> list[dict]:
    """Fetch unread emails from specified senders via IMAP.

    Returns list of dicts with: uid, sender, subject, html_body, date.
    """
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(email_address, app_password)
    conn.select("INBOX")

    all_messages = []

    for sender in sender_addresses:
        criteria = f'(UNSEEN FROM "{sender}")'
        status, data = conn.search(None, criteria)

        if status != "OK" or not data[0]:
            continue

        uids = data[0].split()
        if len(uids) > max_results:
            uids = uids[:max_results]

        for uid in uids:
            status, msg_data = conn.fetch(uid, "(RFC822)")
            if status != "OK":
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            html_body = _extract_html_body(msg)

            all_messages.append({
                "uid": uid.decode(),
                "sender": msg.get("From", ""),
                "subject": msg.get("Subject", ""),
                "html_body": html_body or "",
                "date": msg.get("Date", ""),
            })

    conn.close()
    conn.logout()

    logger.info("Fetched %d unread alert emails", len(all_messages))
    return all_messages


def mark_batch_as_read(
    email_address: str,
    app_password: str,
    uids: list[str],
) -> None:
    """Mark multiple messages as read in a single IMAP session."""
    if not uids:
        return

    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(email_address, app_password)
    conn.select("INBOX")

    for uid in uids:
        conn.store(uid.encode(), "+FLAGS", "\\Seen")

    conn.close()
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
