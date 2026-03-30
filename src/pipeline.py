"""Main pipeline entry points. Run via: python -m src.pipeline <command>"""

import logging
import sys
import time
import random
from pathlib import Path

import requests

from src.config import load_config
from src.storage import (
    read_master,
    write_master,
    deduplicate_and_merge,
)
from src.gmail_client import (
    get_gmail_service,
    fetch_unread_alerts,
    mark_as_read,
    send_html_email,
)
from src.parsers import sreality, ceskereality, idnes
from src.greenery import compute_final_greenery_score
from src.transit import compute_transit_score
from src.scorer import score_dataframe, identify_outliers
from src.reporter import generate_report_html, generate_scatter_plot
from src.drive_sync import (
    get_drive_service,
    find_or_create_folder,
    share_folder,
    upload_csv,
    download_csv,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_parse(config_path: str = "config.yaml") -> None:
    """Fetch alert emails, parse, enrich, deduplicate, save."""
    config = load_config(config_path)

    # Gmail: fetch unread alerts
    gmail = get_gmail_service(config.gmail.credentials_file, config.gmail.token_file)
    emails = fetch_unread_alerts(gmail, config.gmail.alert_senders)

    if not emails:
        logger.info("No new alert emails. Nothing to do.")
        return

    # Parse emails by source
    session = requests.Session()
    session.headers.update({"User-Agent": config.enricher.user_agent})
    delay = tuple(config.enricher.request_delay_seconds)

    all_records = []
    for email in emails:
        sender = email["sender"].lower()
        html = email["html_body"]

        if not html:
            logger.warning("Empty email body from %s, skipping", sender)
            mark_as_read(gmail, email["message_id"])
            continue

        records = []
        if "sreality" in sender:
            parsed = sreality.parse_email_html(html)
            records = sreality.enrich_from_email(parsed, session, delay)
        elif "ceskereality" in sender:
            parsed = ceskereality.parse_email_html(html)
            records = ceskereality.enrich_from_email(parsed, session, delay)
        else:
            # Visidoo or other third-party (idnes)
            parsed = idnes.parse_email_html(html)
            records = idnes.enrich_from_email(parsed, session, delay)

        logger.info(
            "Parsed %d listings from %s (%s)",
            len(records), sender, email["subject"],
        )
        all_records.extend(records)
        mark_as_read(gmail, email["message_id"])

    if not all_records:
        logger.info("No listings extracted from emails.")
        return

    # Enrich with OSM data (greenery + transit)
    for rec in all_records:
        if rec.lat and rec.lon:
            green_score, green_source = compute_final_greenery_score(
                keyword_score=rec.greenery_score or 0,
                keyword_source=rec.greenery_source,
                lat=rec.lat,
                lon=rec.lon,
                osm_radius_m=config.scoring.greenery.osm_search_radius_m,
            )
            rec.greenery_score = green_score
            rec.greenery_source = green_source

            transit_score, dist_km = compute_transit_score(
                rec.lat, rec.lon, rec.district, rec.location,
            )
            rec.distance_to_train_km = dist_km

            # Rate limit OSM queries
            time.sleep(random.uniform(1.0, 2.0))

    # Load existing, merge, save
    master_path = config.data.master_csv
    existing = read_master(master_path)
    merged, new_count, updated_count = deduplicate_and_merge(existing, all_records)
    write_master(merged, master_path)

    logger.info(
        "Master CSV updated: %d new, %d updated, %d total",
        new_count, updated_count, len(merged),
    )


def run_score(config_path: str = "config.yaml") -> None:
    """Run scoring on master CSV."""
    config = load_config(config_path)
    master_path = config.data.master_csv

    df = read_master(master_path)
    if df.empty:
        logger.info("Master CSV is empty. Nothing to score.")
        return

    scored = score_dataframe(df, config.scoring)
    write_master(scored, master_path)
    logger.info("Scored %d properties", len(scored))


def run_report(config_path: str = "config.yaml") -> None:
    """Generate and send the report email."""
    config = load_config(config_path)
    master_path = config.data.master_csv

    df = read_master(master_path)
    if df.empty:
        logger.info("Master CSV is empty. No report to send.")
        return

    # Score first
    scored = score_dataframe(df, config.scoring)
    write_master(scored, master_path)

    # Identify outliers
    outliers = identify_outliers(scored, top_n=10)
    outlier_ids = set(outliers["property_id"].tolist())

    # Generate scatter plot
    scatter_bytes = generate_scatter_plot(scored, outlier_ids)

    # Count new listings (scraped in last 3 days)
    import pandas as pd
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=3)).isoformat()
    new_count = len(scored[scored["scrape_date"] >= cutoff[:10]])

    # Generate HTML
    html = generate_report_html(
        outliers=outliers,
        total_count=len(scored),
        new_count=new_count,
    )

    # Send
    gmail = get_gmail_service(config.gmail.credentials_file, config.gmail.token_file)
    send_html_email(
        service=gmail,
        to=config.gmail.report_recipient,
        subject=f"Property Report -- {datetime.now().strftime('%d %b %Y')}",
        html_body=html,
        inline_images={"scatter_plot": scatter_bytes},
    )
    logger.info("Report sent to %s", config.gmail.report_recipient)


def run_sync(config_path: str = "config.yaml") -> None:
    """Sync master CSV to Google Drive."""
    config = load_config(config_path)
    master_path = Path(config.data.master_csv)

    if not master_path.exists():
        logger.info("No master CSV to sync.")
        return

    drive = get_drive_service(config.gmail.credentials_file, config.gmail.token_file)
    folder_id = find_or_create_folder(drive, config.drive.folder_name)

    # Share with configured emails (idempotent)
    for email in config.drive.share_with:
        share_folder(drive, folder_id, email)

    upload_csv(drive, master_path, folder_id)
    logger.info("Drive sync complete")


def run_download(config_path: str = "config.yaml") -> None:
    """Download master CSV from Google Drive (for GitHub Actions)."""
    config = load_config(config_path)
    master_path = Path(config.data.master_csv)

    drive = get_drive_service(config.gmail.credentials_file, config.gmail.token_file)
    folder_id = find_or_create_folder(drive, config.drive.folder_name)
    download_csv(drive, folder_id, master_path)


def run_daily(config_path: str = "config.yaml") -> None:
    """Full daily pipeline: download -> parse -> score -> sync."""
    logger.info("=== Daily pipeline start ===")
    run_download(config_path)
    run_parse(config_path)
    run_score(config_path)
    run_sync(config_path)
    logger.info("=== Daily pipeline complete ===")


def run_report_all(config_path: str = "config.yaml") -> None:
    """Full report pipeline: download -> parse -> score -> report -> sync."""
    logger.info("=== Report pipeline start ===")
    run_download(config_path)
    run_parse(config_path)
    run_report(config_path)
    run_sync(config_path)
    logger.info("=== Report pipeline complete ===")


COMMANDS = {
    "parse": run_parse,
    "score": run_score,
    "report": run_report,
    "sync": run_sync,
    "download": run_download,
    "daily": run_daily,
    "report-all": run_report_all,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python -m src.pipeline <command>")
        print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    cmd = sys.argv[1]
    config_path = sys.argv[2] if len(sys.argv) > 2 else "config.yaml"
    COMMANDS[cmd](config_path)


if __name__ == "__main__":
    main()
