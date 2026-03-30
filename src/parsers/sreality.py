"""Sreality.cz parser: email alerts + JSON API enrichment."""

import logging
import re
import time
import random
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from src.parsers._shared import (
    detect_greenery_from_text,
    parse_size_category,
    parse_size_sqm,
    normalize_price,
)
from src.storage import PropertyRecord, generate_property_id

logger = logging.getLogger(__name__)

SREALITY_API_BASE = "https://www.sreality.cz/api/cs/v2"
SREALITY_WEB_BASE = "https://www.sreality.cz"


def parse_email_html(html: str) -> list[dict]:
    """Extract listing links and basic info from a sreality alert email.

    Returns list of dicts with keys: url, name, price, locality.
    """
    soup = BeautifulSoup(html, "lxml")
    listings = []

    # Sreality alert emails contain listing links pointing to sreality.cz/detail/...
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/detail/" not in href:
            continue
        # Normalize URL
        if href.startswith("/"):
            href = SREALITY_WEB_BASE + href

        # Extract text context around the link
        parent = link.find_parent(["tr", "div", "td"])
        context_text = parent.get_text(separator=" ", strip=True) if parent else ""

        # Try to extract price from context
        price_match = re.search(r"([\d\s.,]+)\s*Kč", context_text)
        price = normalize_price(price_match.group(1)) if price_match else None

        listings.append({
            "url": href,
            "name": link.get_text(strip=True),
            "price": price,
            "context": context_text,
        })

    # Deduplicate by URL within same email
    seen = set()
    unique = []
    for item in listings:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)

    return unique


def extract_hash_id_from_url(url: str) -> Optional[str]:
    """Extract the numeric hash_id from a sreality detail URL."""
    # URL format: .../detail/prodej/byt/3+kk/praha-smichov/1234567890
    match = re.search(r"/(\d{8,})(?:\?|$|#)", url)
    if match:
        return match.group(1)
    # Try last path segment
    parts = url.rstrip("/").split("/")
    if parts and parts[-1].isdigit():
        return parts[-1]
    return None


def fetch_listing_detail(hash_id: str, session: requests.Session) -> Optional[dict]:
    """Fetch full listing details from sreality JSON API."""
    url = f"{SREALITY_API_BASE}/estates/{hash_id}"
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        logger.warning("Failed to fetch sreality detail for hash_id=%s", hash_id)
        return None


def search_listings(
    category_main: int = 1,  # 1=apartments, 2=houses
    category_type: int = 1,  # 1=sale
    region: str = "Praha",
    per_page: int = 20,
    page: int = 1,
    session: Optional[requests.Session] = None,
) -> Optional[dict]:
    """Search sreality API for listings."""
    session = session or requests.Session()
    params = {
        "category_main_cb": category_main,
        "category_type_cb": category_type,
        "per_page": per_page,
        "page": page,
    }
    url = f"{SREALITY_API_BASE}/estates"
    try:
        resp = session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        logger.warning("Failed to search sreality listings")
        return None


def api_detail_to_record(detail: dict, url: str = "") -> PropertyRecord:
    """Convert sreality API detail response to a PropertyRecord."""
    now = datetime.now().isoformat()
    today = date.today().isoformat()

    # Basic fields
    name = detail.get("name", {})
    if isinstance(name, dict):
        name_text = name.get("value", "")
    else:
        name_text = str(name)

    locality = detail.get("locality", {})
    if isinstance(locality, dict):
        locality_text = locality.get("value", "")
    else:
        locality_text = str(locality)

    price = detail.get("price_czk", {})
    if isinstance(price, dict):
        price_val = price.get("value_raw")
    else:
        price_val = price

    # GPS
    lat = detail.get("map", {}).get("lat")
    lon = detail.get("map", {}).get("lon")

    # Extract from items/attributes
    size_sqm = None
    energy_rating = ""
    description_text = ""
    garden_detected = False

    for item in detail.get("items", []):
        item_name = item.get("name", "")
        item_value = item.get("value", "")

        if "Užitná plocha" in str(item_name) or "Plocha" in str(item_name):
            if isinstance(item_value, (int, float)):
                size_sqm = float(item_value)
            elif isinstance(item_value, str):
                num = re.search(r"(\d+)", item_value)
                if num:
                    size_sqm = float(num.group(1))

        if "PENB" in str(item_name) or "Energetická" in str(item_name):
            energy_rating = str(item_value).strip().upper()
            if energy_rating and energy_rating[0] in "ABCDEFG":
                energy_rating = energy_rating[0]

        if "Popis" in str(item_name) or "Description" in str(item_name):
            description_text = str(item_value)

        if "zahrad" in str(item_value).lower():
            garden_detected = True

    # Check text description for greenery
    full_text = f"{name_text} {description_text} {locality_text}"
    greenery_type, greenery_score = detect_greenery_from_text(full_text)
    if garden_detected and greenery_score < 95:
        greenery_type = "private_garden"
        greenery_score = 95

    # Determine property type
    seo = detail.get("seo", {})
    category_text = str(seo.get("category_main_cb", ""))
    if "Dům" in category_text or "dům" in category_text or "house" in category_text:
        property_type = "house"
    else:
        property_type = "apartment"

    size_cat = parse_size_category(name_text)
    if not size_sqm:
        size_sqm = parse_size_sqm(name_text)

    price_total = normalize_price(price_val) if price_val else None
    price_per_sqm = None
    if price_total and size_sqm and size_sqm > 0:
        price_per_sqm = round(price_total / size_sqm, 2)

    hash_id = str(detail.get("_embedded", {}).get("hash_id", ""))
    if not url and hash_id:
        url = f"{SREALITY_WEB_BASE}/detail/-/-/-/-/{hash_id}"

    pid = generate_property_id("sreality", url)

    return PropertyRecord(
        property_id=pid,
        source="sreality",
        url=url,
        property_type=property_type,
        size_category=size_cat or "",
        size_sqm=size_sqm,
        garden_present=garden_detected or greenery_type == "private_garden",
        greenery_score=greenery_score,
        greenery_source=f"keyword:{greenery_type}" if greenery_score > 0 else "",
        price_total=price_total,
        price_per_sqm=price_per_sqm,
        location=locality_text,
        district=_extract_district(locality_text),
        lat=lat,
        lon=lon,
        energy_efficiency_rating=energy_rating,
        scrape_date=today,
        last_updated=now,
        enrichment_status="done",
    )


def enrich_from_email(
    email_listings: list[dict],
    session: requests.Session,
    delay_range: tuple[float, float] = (2.0, 4.0),
) -> list[PropertyRecord]:
    """Take parsed email listings, enrich via API, return PropertyRecords."""
    records = []

    for item in email_listings:
        url = item["url"]
        hash_id = extract_hash_id_from_url(url)

        if not hash_id:
            logger.warning("Could not extract hash_id from URL: %s", url)
            # Create minimal record from email data
            records.append(_minimal_record_from_email(item))
            continue

        detail = fetch_listing_detail(hash_id, session)
        if detail:
            record = api_detail_to_record(detail, url=url)
            records.append(record)
        else:
            records.append(_minimal_record_from_email(item))

        time.sleep(random.uniform(*delay_range))

    return records


def _minimal_record_from_email(item: dict) -> PropertyRecord:
    """Create a partial record when API enrichment fails."""
    url = item.get("url", "")
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    name = item.get("name", "")

    return PropertyRecord(
        property_id=generate_property_id("sreality", url),
        source="sreality",
        url=url,
        size_category=parse_size_category(name) or "",
        size_sqm=parse_size_sqm(name),
        price_total=item.get("price"),
        location=item.get("context", ""),
        scrape_date=today,
        last_updated=now,
        enrichment_status="failed",
    )


def _extract_district(locality: str) -> str:
    """Extract district from locality string like 'Na Plzenci, Praha 5 - Smichov'."""
    match = re.search(r"(Praha\s*\d+)", locality)
    if match:
        return match.group(1)
    # For non-Prague, take the last meaningful part
    parts = [p.strip() for p in locality.split(",")]
    if parts:
        return parts[-1].split("-")[0].strip()
    return ""
