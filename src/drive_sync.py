"""Google Drive sync for master CSV."""

import logging
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def get_drive_service(
    credentials_file: str = "credentials.json",
    token_file: str = "token.json",
):
    """Authenticate and return Google Drive API service.

    Note: shares OAuth credentials with Gmail client. The token file
    should contain both Gmail and Drive scopes.
    """
    creds = None
    token_path = Path(token_file)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path))

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            all_scopes = SCOPES + [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/gmail.send",
            ]
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_file, all_scopes
            )
            creds = flow.run_local_server(port=0)

        token_path.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def find_or_create_folder(service, folder_name: str) -> str:
    """Find existing folder or create new one. Returns folder ID."""
    query = (
        f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    results = service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    files = results.get("files", [])

    if files:
        folder_id = files[0]["id"]
        logger.info("Found existing folder '%s' (id=%s)", folder_name, folder_id)
        return folder_id

    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    folder_id = folder["id"]
    logger.info("Created folder '%s' (id=%s)", folder_name, folder_id)
    return folder_id


def share_folder(service, folder_id: str, email: str) -> None:
    """Share a folder with an email address (writer access)."""
    permission = {
        "type": "user",
        "role": "writer",
        "emailAddress": email,
    }
    try:
        service.permissions().create(
            fileId=folder_id,
            body=permission,
            sendNotificationEmail=True,
        ).execute()
        logger.info("Shared folder with %s", email)
    except Exception:
        logger.warning("Failed to share folder with %s (may already be shared)", email)


def upload_csv(
    service,
    local_path: str | Path,
    folder_id: str,
    filename: str = "master.csv",
) -> str:
    """Upload or update CSV file in Drive folder. Returns file ID."""
    local_path = Path(local_path)
    if not local_path.exists():
        msg = f"File not found: {local_path}"
        raise FileNotFoundError(msg)

    # Check if file already exists in folder
    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    results = service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    existing = results.get("files", [])

    media = MediaFileUpload(str(local_path), mimetype="text/csv")

    if existing:
        file_id = existing[0]["id"]
        service.files().update(
            fileId=file_id,
            media_body=media,
        ).execute()
        logger.info("Updated %s in Drive (id=%s)", filename, file_id)
        return file_id
    else:
        metadata = {
            "name": filename,
            "parents": [folder_id],
        }
        result = service.files().create(
            body=metadata,
            media_body=media,
            fields="id",
        ).execute()
        file_id = result["id"]
        logger.info("Uploaded %s to Drive (id=%s)", filename, file_id)
        return file_id


def download_csv(
    service,
    folder_id: str,
    local_path: str | Path,
    filename: str = "master.csv",
) -> bool:
    """Download CSV from Drive folder to local path. Returns True if found."""
    local_path = Path(local_path)

    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    results = service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    existing = results.get("files", [])

    if not existing:
        logger.info("No %s found in Drive folder", filename)
        return False

    file_id = existing[0]["id"]
    request = service.files().get_media(fileId=file_id)

    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    logger.info("Downloaded %s from Drive", filename)
    return True
