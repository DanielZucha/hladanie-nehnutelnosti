"""Main pipeline entry points. Run via: python -m src.pipeline <command>"""

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from src.config import AppConfig, load_config
from src.drive_sync import download_csv, ensure_remote_folder, upload_csv
from src.gmail_client import fetch_unread_alerts, mark_batch_as_read, send_html_email
from src.locate import locate_records
from src.parsers import ceskereality, idnes, sreality
from src.region_filter import filter_records
from src.reporter import generate_report_html, generate_scatter_plot
from src.scorer import identify_categorized_picks, score_dataframe
from src.storage import PropertyRecord, deduplicate_and_merge, read_master, write_master

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _session(config: AppConfig) -> requests.Session:
    """HTTP session with the configured browser user agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": config.enricher.user_agent})
    return session


def _merge(config: AppConfig, records: list[PropertyRecord], label: str) -> None:
    """Apply hard rules, then merge survivors into the master CSV."""
    records = filter_records(records, config.search)
    if not records:
        logger.info("%s: nothing passed the rules", label)
        return
    path = config.data.master_csv
    merged, new_count, updated_count = deduplicate_and_merge(read_master(path), records)
    write_master(merged, path)
    logger.info("%s: %d new, %d updated, %d total", label, new_count, updated_count, len(merged))


def run_parse(config_path: str = "config.yaml") -> None:
    """Fetch iDNES/ceskereality alert emails, enrich from detail pages, locate, merge."""
    config = load_config(config_path)
    emails = fetch_unread_alerts(
        config.email.address, config.email.app_password, config.email.alert_senders,
    )
    if not emails:
        logger.info("No new alert emails.")
        return

    session = _session(config)
    delay = tuple(config.enricher.request_delay_seconds)
    records: list[PropertyRecord] = []
    processed: list = []

    for em in emails:
        sender, html = em["sender"].lower(), em["html_body"]
        if html and "ceskereality" in sender:
            records.extend(ceskereality.enrich_from_email(ceskereality.parse_email_html(html), session, delay))
        elif html and "idnes" in sender:
            records.extend(idnes.email_listings_to_records(idnes.parse_email_html(html), session, delay))
        else:
            logger.info("Skipping email from %s", sender)
        processed.append(em["uid"])

    # Mark read only after a successful merge, so a failed run retries the same alerts.
    _merge(config, locate_records(records), "Email alerts")
    mark_batch_as_read(config.email.address, config.email.app_password, processed)


def run_scrape(config_path: str = "config.yaml") -> None:
    """Fetch the full filtered sreality inventory and merge; first-seen date marks new listings."""
    config = load_config(config_path)
    records = sreality.scrape_listings(_session(config), config.search)
    _merge(config, locate_records(records), "sreality")  # station snapshot backs up missing poi_train_distance


def run_score(config_path: str = "config.yaml") -> None:
    """Re-score the master CSV."""
    config = load_config(config_path)
    df = read_master(config.data.master_csv)
    if df.empty:
        logger.info("Master CSV is empty. Nothing to score.")
        return
    write_master(score_dataframe(df, config.scoring), config.data.master_csv)


def report_cutoff(today: date) -> str:
    """Listings first seen after this date are new: Tue looks back to Fri, Fri (and manual runs) 3 days."""
    days = 4 if today.weekday() == 1 else 3
    return (today - timedelta(days=days)).isoformat()


def run_report(config_path: str = "config.yaml") -> None:
    """Score, pick and email the report for listings first seen in the last 4 days."""
    config = load_config(config_path)
    df = read_master(config.data.master_csv)
    if df.empty:
        logger.info("Master CSV is empty. No report to send.")
        return

    scored = score_dataframe(df, config.scoring)
    write_master(scored, config.data.master_csv)

    new_listings = scored[scored["scrape_date"] > report_cutoff(date.today())]
    if new_listings.empty:
        logger.info("No new listings since last report. Skipping.")
        return

    picks = identify_categorized_picks(new_listings, per_category=5)
    pick_ids = {pid for cat in picks.values() for pid in cat["property_id"]}
    html = generate_report_html(
        categorized_picks=picks,
        total_count=len(scored),
        new_count=len(new_listings),
        weights=config.scoring.weights,
    )
    send_html_email(
        email_address=config.email.address,
        app_password=config.email.app_password,
        to=config.email.report_recipients,
        subject=f"Přehled nemovitostí -- {datetime.now().strftime('%d. %m. %Y')}",
        html_body=html,
        inline_images={"scatter_plot": generate_scatter_plot(scored, pick_ids)},
    )
    logger.info("Report sent to %d recipients", len(config.email.report_recipients))


def run_sync_up(config_path: str = "config.yaml") -> None:
    """Upload master CSV to Google Drive via rclone."""
    config = load_config(config_path)
    master_path = Path(config.data.master_csv)
    if not master_path.exists():
        logger.info("No master CSV to upload.")
        return
    ensure_remote_folder(config.drive.remote_folder)
    upload_csv(master_path, config.drive.remote_folder)


def run_sync_down(config_path: str = "config.yaml") -> None:
    """Download master CSV from Google Drive via rclone."""
    config = load_config(config_path)
    ensure_remote_folder(config.drive.remote_folder)
    download_csv(config.drive.remote_folder, Path(config.data.master_csv))


def _ingest(config_path: str) -> list[str]:
    """Run both sources; one failing must not stop the other. Returns error messages."""
    errors = []
    for step in (run_parse, run_scrape):
        try:
            step(config_path)
        except Exception as exc:  # report at the end so the run is marked failed
            logger.exception("%s failed", step.__name__)
            errors.append(f"{step.__name__}: {exc}")
    return errors


def _finish(errors: list[str]) -> None:
    """Exit non-zero when a source failed, so GitHub emails a failure notice."""
    if errors:
        logger.error("Completed with errors: %s", errors)
        sys.exit(1)


def run_daily(config_path: str = "config.yaml") -> None:
    """Daily: pull CSV -> emails + sreality -> score -> push CSV."""
    run_sync_down(config_path)
    errors = _ingest(config_path)
    run_score(config_path)
    run_sync_up(config_path)
    _finish(errors)


def run_report_all(config_path: str = "config.yaml") -> None:
    """Report day: pull CSV -> emails + sreality -> report -> push CSV."""
    run_sync_down(config_path)
    errors = _ingest(config_path)
    run_report(config_path)
    run_sync_up(config_path)
    _finish(errors)


COMMANDS = {
    "parse": run_parse,
    "scrape": run_scrape,
    "score": run_score,
    "report": run_report,
    "sync-up": run_sync_up,
    "sync-down": run_sync_down,
    "daily": run_daily,
    "report-all": run_report_all,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python -m src.pipeline <command>")
        print(f"Commands: {', '.join(COMMANDS)}")
        sys.exit(1)
    config_path = sys.argv[2] if len(sys.argv) > 2 else "config.yaml"
    COMMANDS[sys.argv[1]](config_path)


if __name__ == "__main__":
    main()
