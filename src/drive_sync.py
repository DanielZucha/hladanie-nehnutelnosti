"""Google Drive sync via rclone.

rclone has its own built-in OAuth client for Google Drive -- no Google Cloud
Console setup needed. One-time setup:

    brew install rclone
    rclone config
    -> New remote, name it "homesearch", type "drive", authorize with throwaway Gmail

For GitHub Actions, store just this remote's config as a secret:
    rclone config show homesearch | base64  -> RCLONE_CONFIG_B64 secret
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Override via RCLONE_REMOTE env var if needed
REMOTE_NAME = os.environ.get("RCLONE_REMOTE", "hladanie-nehnutelnosti")


def _run_rclone(args: list[str]) -> subprocess.CompletedProcess:
    """Run an rclone command, raising on failure."""
    cmd = ["rclone"] + args
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error("rclone failed: %s", result.stderr)
        result.check_returncode()
    return result


def ensure_remote_folder(folder_name: str) -> str:
    """Ensure the remote folder exists. Returns the remote path."""
    remote_path = f"{REMOTE_NAME}:{folder_name}"
    _run_rclone(["mkdir", remote_path])
    return remote_path


def download_csv(
    remote_folder: str,
    local_path: str | Path,
    filename: str = "master.csv",
) -> bool:
    """Download master CSV from Google Drive via rclone.

    Returns True if file was downloaded.
    """
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    remote = f"{REMOTE_NAME}:{remote_folder}/{filename}"

    try:
        _run_rclone(["copyto", remote, str(local_path)])
        if local_path.exists() and local_path.stat().st_size > 0:
            logger.info("Downloaded %s from Drive", filename)
            return True
    except subprocess.CalledProcessError:
        logger.info("No %s found in Drive (first run?)", filename)

    return False


def upload_csv(
    local_path: str | Path,
    remote_folder: str,
    filename: str = "master.csv",
) -> None:
    """Upload master CSV to Google Drive via rclone."""
    local_path = Path(local_path)
    if not local_path.exists():
        logger.info("No file to upload: %s", local_path)
        return

    remote = f"{REMOTE_NAME}:{remote_folder}/{filename}"
    _run_rclone(["copyto", str(local_path), remote])
    logger.info("Uploaded %s to Drive", filename)
