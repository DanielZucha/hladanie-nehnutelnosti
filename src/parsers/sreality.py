"""Sreality.cz parser: email alerts link to saved searches, not individual listings.

The email contains links like:
    https://sreality.cz/ulozene-hledani/4048096?utm_source=...
    "5 novych inzeratu" / "61 novych inzeratu"

Strategy:
1. Extract saved search IDs from email
2. For each saved search, query the sreality JSON API to get the actual listings
3. The API endpoint /api/cs/v2/estates returns full listing data including GPS
"""

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
    """Extract saved search URLs and listing counts from sreality email.

    Returns list of dicts with: search_url, search_id, count.
    """
    soup = BeautifulSoup(html, "lxml")
    searches = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "ulozene-hledani" not in href:
            continue

        # Extract search ID
        id_match = re.search(r"ulozene-hledani/(\d+)", href)
        if not id_match:
            continue

        search_id = id_match.group(1)

        # Extract count from link text: "5  nových inzerátů"
        text = link.get_text(strip=True)
        count_match = re.search(r"(\d+)", text)
        count = int(count_match.group(1)) if count_match else 0

        # Get context text (search criteria description)
        parent = link.find_parent(["td", "div"])
        context = ""
        if parent:
            prev = parent.find_previous(["td", "div"])
            if prev:
                context = prev.get_text(strip=True)

        searches.append({
            "search_url": href.split("?")[0],
            "search_id": search_id,
            "count": count,
            "context": context,
        })

    return searches


def fetch_saved_search_listings(
    search_id: str,
    session: requests.Session,
    region_id: int = 10,
    district_ids: str = "56|51|55",
    max_pages: int = 3,
    per_page: int = 20,
) -> list[dict]:
    """Fetch listings from a saved search via the sreality API.

    The saved search page is a React SPA, but we can query the API directly.
    We try the estates endpoint with pagination to get recent listings.
    Region/district IDs are passed as belt-and-suspenders filter alongside
    the watchdog param (which alone does not filter results).
    """
    all_estates = []

    for page in range(1, max_pages + 1):
        url = f"{SREALITY_API_BASE}/estates"
        params = {
            "category_type_cb": 1,  # sale
            "locality_region_id": region_id,
            "locality_district_id": district_ids,
            "per_page": per_page,
            "page": page,
            "watchdog": search_id,
        }

        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                estates = data.get("_embedded", {}).get("estates", [])
                all_estates.extend(estates)

                result_size = data.get("result_size", 0)
                if len(all_estates) >= result_size:
                    break
            else:
                logger.warning(
                    "Sreality API returned %d for search %s page %d",
                    resp.status_code, search_id, page,
                )
                break
        except requests.RequestException:
            logger.warning("Failed to fetch sreality API for search %s", search_id)
            break

        time.sleep(random.uniform(1.0, 2.0))

    return all_estates


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


def estate_summary_to_record(estate: dict) -> PropertyRecord:
    """Convert a sreality API estate summary (from search results) to PropertyRecord.

    This uses the summary data available in search results. For fuller data,
    call fetch_listing_detail with the hash_id.
    """
    now = datetime.now().isoformat()
    today = date.today().isoformat()

    name = estate.get("name", "")
    locality = estate.get("locality", "")
    hash_id = str(estate.get("hash_id", ""))
    price = estate.get("price", 0)

    # GPS coordinates
    gps = estate.get("gps", {})
    lat = gps.get("lat")
    lon = gps.get("lon")

    # Labels/tags can indicate features (may be strings or dicts)
    raw_labels = estate.get("labels", [])
    labels = []
    for l in raw_labels:
        if isinstance(l, dict):
            labels.append(l.get("name", ""))
        else:
            labels.append(str(l))
    labels_text = " ".join(labels).lower()

    # Build proper URL from SEO data
    seo = estate.get("seo", {})
    url = _build_detail_url(hash_id, seo)

    # Property type from SEO category or name
    category_main = seo.get("category_main_cb", 0)
    name_lower = name.lower()
    if category_main == 2 or "dům" in name_lower or "dum" in name_lower or "rodinný" in name_lower:
        property_type = "house"
    else:
        property_type = "apartment"

    size_cat = parse_size_category(name)
    size_sqm = parse_size_sqm(name)

    price_total = normalize_price(price)
    price_per_sqm = None
    if price_total and size_sqm and size_sqm > 0:
        price_per_sqm = round(price_total / size_sqm, 2)

    # Greenery from labels and name
    full_text = f"{name} {labels_text} {locality}"
    greenery_type, greenery_score = detect_greenery_from_text(full_text)

    # Houses likely have garden
    if property_type == "house" and greenery_score < 80:
        greenery_type = "private_garden"
        greenery_score = 80

    return PropertyRecord(
        property_id=generate_property_id("sreality", url),
        source="sreality",
        url=url,
        property_type=property_type,
        size_category=size_cat or "",
        size_sqm=size_sqm,
        garden_present=greenery_type in ("private_garden", "shared_garden"),
        greenery_score=greenery_score,
        greenery_source=f"keyword:{greenery_type}" if greenery_score > 0 else "",
        price_total=price_total,
        price_per_sqm=price_per_sqm,
        location=locality,
        district=_extract_district(locality),
        lat=lat,
        lon=lon,
        energy_efficiency_rating="",
        scrape_date=today,
        last_updated=now,
        enrichment_status="summary",
    )


def enrich_record_with_detail(
    record: PropertyRecord,
    session: requests.Session,
) -> PropertyRecord:
    """Enrich a summary record with full detail from the API."""
    hash_id = record.url.rstrip("/").split("/")[-1]
    detail = fetch_listing_detail(hash_id, session)
    if not detail:
        return record

    # Extract richer data from detail response
    description = ""
    for item in detail.get("items", []):
        item_name = str(item.get("name", ""))
        item_value = item.get("value", "")

        if "Užitná plocha" in item_name or "Plocha" in item_name:
            if isinstance(item_value, (int, float)):
                record.size_sqm = float(item_value)
            elif isinstance(item_value, str):
                num = re.search(r"(\d+)", item_value)
                if num:
                    record.size_sqm = float(num.group(1))

        if "PENB" in item_name or "Energetická" in item_name:
            rating = str(item_value).strip().upper()
            if rating and rating[0] in "ABCDEFG":
                record.energy_efficiency_rating = rating[0]

        if "Popis" in item_name:
            description = str(item_value)

        if "zahrad" in str(item_value).lower():
            record.garden_present = True
            if (record.greenery_score or 0) < 95:
                record.greenery_score = 95
                record.greenery_source = "keyword:private_garden"

    # Re-check greenery from description
    if description:
        g_type, g_score = detect_greenery_from_text(description)
        if g_score > (record.greenery_score or 0):
            record.greenery_score = g_score
            record.greenery_source = f"keyword:{g_type}"
            record.garden_present = g_type in ("private_garden", "shared_garden")

    # Recalculate price/sqm if we got better size data
    if record.price_total and record.size_sqm and record.size_sqm > 0:
        record.price_per_sqm = round(record.price_total / record.size_sqm, 2)

    record.enrichment_status = "done"
    record.last_updated = datetime.now().isoformat()

    return record


def process_email(
    html: str,
    session: requests.Session,
    region_id: int = 10,
    district_ids: str = "56|51|55",
    delay_range: tuple[float, float] = (2.0, 4.0),
    enrich_details: bool = False,
) -> list[PropertyRecord]:
    """Full pipeline: parse email -> fetch listings via API -> return records.

    If enrich_details is True, also fetches individual listing details
    (slower but gets description, energy rating, etc.)
    """
    searches = parse_email_html(html)
    if not searches:
        logger.info("No saved search links found in sreality email")
        return []

    all_records = []
    for search in searches:
        logger.info(
            "Fetching sreality search %s (%d new listings): %s",
            search["search_id"], search["count"], search["context"][:80],
        )

        estates = fetch_saved_search_listings(
            search["search_id"], session,
            region_id=region_id,
            district_ids=district_ids,
        )

        for estate in estates:
            record = estate_summary_to_record(estate)
            if enrich_details:
                record = enrich_record_with_detail(record, session)
                time.sleep(random.uniform(*delay_range))
            all_records.append(record)

    logger.info("Total sreality records from email: %d", len(all_records))
    return all_records


def scrape_region_listings(
    session: requests.Session,
    region_id: int = 10,
    district_ids: str = "56|51|55",
    per_page: int = 500,
    delay_range: tuple[float, float] = (1.0, 2.0),
) -> list[PropertyRecord]:
    """Scrape full regional inventory from the sreality API.

    Paginates through all sale listings in the given region + districts.
    Returns summary-level PropertyRecords (no individual detail enrichment).
    """
    all_records = []
    page = 1

    while True:
        url = f"{SREALITY_API_BASE}/estates"
        params = {
            "category_type_cb": 1,  # sale
            "locality_region_id": region_id,
            "locality_district_id": district_ids,
            "per_page": per_page,
            "page": page,
        }

        try:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                logger.warning(
                    "Sreality API returned %d on page %d, stopping",
                    resp.status_code, page,
                )
                break

            data = resp.json()
            estates = data.get("_embedded", {}).get("estates", [])
            if not estates:
                break

            for estate in estates:
                all_records.append(estate_summary_to_record(estate))

            result_size = data.get("result_size", 0)
            logger.info(
                "Sreality scrape page %d: %d estates (total so far: %d / %d)",
                page, len(estates), len(all_records), result_size,
            )

            if len(all_records) >= result_size:
                break

        except requests.RequestException:
            logger.warning("Failed to fetch sreality API page %d", page)
            break

        page += 1
        time.sleep(random.uniform(*delay_range))

    logger.info("Sreality region scrape complete: %d records", len(all_records))
    return all_records


_SEO_TYPE = {1: "prodej", 2: "pronajem"}
_SEO_MAIN = {1: "byt", 2: "dum", 3: "pozemek", 4: "komercni", 5: "ostatni"}
_SEO_SUB = {
    # Byty (apartments)
    2: "1+kk", 3: "1+1", 4: "2+kk", 5: "2+1",
    6: "3+kk", 7: "3+1", 8: "4+kk", 9: "4+1",
    10: "5+kk", 11: "5+1", 12: "6-a-vice", 16: "atypicky",
    # Domy (houses)
    33: "chata", 34: "pamatka", 35: "na-klic",
    37: "rodinny", 38: "cinzovni-dum", 39: "vila",
    43: "chalupa", 44: "zemedelska-usedlost",
    47: "rodinny", 52: "projekt",
    54: "vicegeneracni",
    # Pozemky (land)
    18: "bydleni", 19: "komercni", 20: "pole",
    21: "lesy", 22: "rybnik", 23: "sady-vinice",
    24: "zahrada", 46: "ostatni-pozemky",
    # Komercni (commercial)
    25: "kancelare", 26: "sklady", 27: "vyrobni-prostory",
    28: "obchodni-prostory", 29: "ubytovani",
    30: "restaurace", 31: "zemedelsky-objekt",
    32: "cinzovni-dum", 36: "virtualni-kancelar",
    40: "ordinace", 49: "ostatni-komercni-prostory",
}


def _build_detail_url(hash_id: str, seo: dict) -> str:
    """Build a working sreality.cz detail URL from SEO data.

    URL pattern: /detail/prodej/byt/3+kk/praha-vinohrady/2184995660
    Requires exactly 4 path segments after /detail/ to avoid 404.
    """
    type_str = _SEO_TYPE.get(seo.get("category_type_cb", 1), "prodej")
    main_str = _SEO_MAIN.get(seo.get("category_main_cb", 1), "byt")
    sub_cb = seo.get("category_sub_cb", 0)
    sub_str = _SEO_SUB.get(sub_cb, "")
    locality_str = seo.get("locality", "")

    if not sub_str:
        logger.warning("Unknown category_sub_cb=%s for hash_id=%s, using 'x'", sub_cb, hash_id)
        sub_str = "x"

    if not locality_str:
        locality_str = "x"

    return f"{SREALITY_WEB_BASE}/detail/{type_str}/{main_str}/{sub_str}/{locality_str}/{hash_id}"


def _extract_district(locality: str) -> str:
    """Extract district from locality string."""
    match = re.search(r"(Praha\s*\d+)", locality)
    if match:
        return match.group(1)
    # "okres X" pattern
    okres_match = re.search(r"okres\s+([^,]+)", locality)
    if okres_match:
        return okres_match.group(1).strip()
    parts = [p.strip() for p in locality.split(",")]
    if parts:
        return parts[-1].split("-")[0].strip()
    return ""
