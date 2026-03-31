"""Main pipeline entry points. Run via: python -m src.pipeline <command>"""

import logging
import sys
import time
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from src.config import load_config
from src.storage import read_master, write_master, deduplicate_and_merge
from src.gmail_client import (
    fetch_unread_alerts,
    mark_batch_as_read,
    send_html_email,
)
from src.parsers import sreality, ceskereality, idnes
from src.greenery import compute_final_greenery_score
from src.transit import compute_transit_score
from src.scorer import score_dataframe, identify_outliers, identify_categorized_picks
from src.reporter import generate_report_html, generate_scatter_plot
from src.drive_sync import ensure_remote_folder, download_csv, upload_csv
from src.region_filter import filter_records

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_parse(config_path: str = "config.yaml") -> None:
    """Fetch alert emails, parse, enrich, deduplicate, save."""
    config = load_config(config_path)

    emails = fetch_unread_alerts(
        config.email.address,
        config.email.app_password,
        config.email.alert_senders,
    )

    if not emails:
        logger.info("No new alert emails. Nothing to do.")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": config.enricher.user_agent})
    delay = tuple(config.enricher.request_delay_seconds)

    all_records = []
    processed_uids = []

    for em in emails:
        sender = em["sender"].lower()
        html = em["html_body"]

        if not html:
            logger.warning("Empty email body from %s, skipping", sender)
            processed_uids.append(em["uid"])
            continue

        records = []
        if "sreality" in sender:
            # Skip: sreality listings are covered by daily API scrape
            logger.info("Skipping sreality email (covered by API scrape)")
        elif "ceskereality" in sender:
            parsed = ceskereality.parse_email_html(html)
            records = ceskereality.enrich_from_email(parsed, session, delay)
        elif "idnes" in sender:
            parsed = idnes.parse_email_html(html)
            records = idnes.email_listings_to_records(parsed, session, delay)
        else:
            logger.warning("Unknown sender: %s, skipping", sender)
            continue

        logger.info(
            "Parsed %d listings from %s (%s)",
            len(records), sender, em["subject"],
        )
        all_records.extend(records)
        processed_uids.append(em["uid"])

    # Mark processed emails as read
    mark_batch_as_read(
        config.email.address,
        config.email.app_password,
        processed_uids,
    )

    if not all_records:
        logger.info("No listings extracted from emails.")
        return

    # Drop listings outside target regions (Praha + Středočeský kraj)
    all_records = filter_records(all_records)

    if not all_records:
        logger.info("All listings filtered out by region filter.")
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

    scored = score_dataframe(df, config.scoring)
    write_master(scored, master_path)

    # Report only on new arrivals since last report.
    # Reports run Tue + Fri, so the window is 3-4 days.
    # Use 4 days to ensure no gap between Fri->Tue.
    cutoff = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")
    new_listings = scored[scored["scrape_date"] >= cutoff]

    if new_listings.empty:
        logger.info("No new listings since last report. Skipping.")
        return

    picks = identify_categorized_picks(new_listings, per_category=5)

    # Scatter plot: new arrivals highlighted against full dataset
    all_pick_ids = set()
    for cat_df in picks.values():
        all_pick_ids.update(cat_df["property_id"].tolist())

    scatter_bytes = generate_scatter_plot(scored, all_pick_ids)

    html = generate_report_html(
        categorized_picks=picks,
        total_count=len(scored),
        new_count=len(new_listings),
    )

    send_html_email(
        email_address=config.email.address,
        app_password=config.email.app_password,
        to=config.email.report_recipients,
        subject=f"Přehled nemovitostí -- {datetime.now().strftime('%d. %m. %Y')}",
        html_body=html,
        inline_images={"scatter_plot": scatter_bytes},
    )
    logger.info("Report sent to %s", config.email.report_recipients)


def run_scrape(config_path: str = "config.yaml") -> None:
    """Scrape new sreality arrivals, OSM-enrich, deduplicate, save."""
    config = load_config(config_path)

    session = requests.Session()
    session.headers.update({"User-Agent": config.enricher.user_agent})

    logger.info("Scraping new sreality arrivals...")
    all_records = sreality.scrape_new_listings(
        session,
        region_id=config.search.sreality_region_id,
        district_ids=config.search.sreality_district_ids,
        price_max=config.search.price_max_czk,
    )

    if not all_records:
        logger.info("No new sreality listings today.")
        return

    all_records = filter_records(all_records)

    if not all_records:
        logger.info("All new listings filtered out by region filter.")
        return

    # OSM enrich all new arrivals (~35-50/day, well within Overpass limits)
    master_path = config.data.master_csv
    existing = read_master(master_path)
    existing_ids = set(existing["property_id"].tolist()) if not existing.empty else set()

    enriched_count = 0
    for rec in all_records:
        if rec.property_id in existing_ids:
            continue
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
            enriched_count += 1

            time.sleep(random.uniform(1.0, 2.0))

    logger.info("OSM-enriched %d new sreality records", enriched_count)

    merged, new_count, updated_count = deduplicate_and_merge(existing, all_records)
    write_master(merged, master_path)

    logger.info(
        "Scrape merge: %d new, %d price-updated, %d total",
        new_count, updated_count, len(merged),
    )


def run_scrape_full(config_path: str = "config.yaml") -> None:
    """One-time full regional inventory scrape. No OSM enrichment."""
    config = load_config(config_path)

    session = requests.Session()
    session.headers.update({"User-Agent": config.enricher.user_agent})

    logger.info("Starting full sreality region scrape...")
    all_records = sreality.scrape_region_listings(
        session,
        region_id=config.search.sreality_region_id,
        district_ids=config.search.sreality_district_ids,
        price_max=config.search.price_max_czk,
    )

    if not all_records:
        logger.info("No listings from region scrape.")
        return

    all_records = filter_records(all_records)
    if not all_records:
        return

    master_path = config.data.master_csv
    existing = read_master(master_path)
    merged, new_count, updated_count = deduplicate_and_merge(existing, all_records)
    write_master(merged, master_path)

    logger.info(
        "Full scrape: %d new, %d price-updated, %d total",
        new_count, updated_count, len(merged),
    )


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
    master_path = Path(config.data.master_csv)

    ensure_remote_folder(config.drive.remote_folder)
    download_csv(config.drive.remote_folder, master_path)


def run_daily(config_path: str = "config.yaml") -> None:
    """Full daily pipeline: pull CSV -> parse emails -> scrape region -> score -> push CSV."""
    logger.info("=== Daily pipeline start ===")
    run_sync_down(config_path)
    run_parse(config_path)
    run_scrape(config_path)
    run_score(config_path)
    run_sync_up(config_path)
    logger.info("=== Daily pipeline complete ===")


def run_report_all(config_path: str = "config.yaml") -> None:
    """Full report pipeline: pull CSV -> parse -> scrape -> score -> report -> push CSV."""
    logger.info("=== Report pipeline start ===")
    run_sync_down(config_path)
    run_parse(config_path)
    run_scrape(config_path)
    run_report(config_path)
    run_sync_up(config_path)
    logger.info("=== Report pipeline complete ===")


COMMANDS = {
    "parse": run_parse,
    "scrape": run_scrape,
    "scrape-full": run_scrape_full,
    "score": run_score,
    "report": run_report,
    "sync-up": run_sync_up,
    "sync-down": run_sync_down,
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
