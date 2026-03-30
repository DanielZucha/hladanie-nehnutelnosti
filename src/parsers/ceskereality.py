"""Ceskereality.cz parser: email alerts + HTML page enrichment."""

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

CESKEREALITY_BASE = "https://www.ceskereality.cz"


def parse_email_html(html: str) -> list[dict]:
    """Extract listing links and basic info from a ceskereality Hlidace email."""
    soup = BeautifulSoup(html, "lxml")
    listings = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "ceskereality.cz" not in href and not href.startswith("/"):
            continue
        if "/detail/" not in href and "/prodej/" not in href:
            continue

        if href.startswith("/"):
            href = CESKEREALITY_BASE + href

        parent = link.find_parent(["tr", "div", "td"])
        context_text = parent.get_text(separator=" ", strip=True) if parent else ""

        price_match = re.search(r"([\d\s.,]+)\s*Kč", context_text)
        price = normalize_price(price_match.group(1)) if price_match else None

        listings.append({
            "url": href,
            "name": link.get_text(strip=True),
            "price": price,
            "context": context_text,
        })

    seen = set()
    unique = []
    for item in listings:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)

    return unique


def enrich_from_page(
    url: str,
    session: requests.Session,
) -> Optional[PropertyRecord]:
    """Fetch a ceskereality listing page and extract structured data."""
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Failed to fetch ceskereality page: %s", url)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    now = datetime.now().isoformat()
    today = date.today().isoformat()

    # Try JSON-LD first (most reliable)
    record_data = _parse_jsonld(soup)

    # Fallback to HTML parsing
    if not record_data:
        record_data = _parse_html(soup)

    if not record_data:
        return None

    # Greenery detection from full page text
    page_text = soup.get_text(separator=" ")
    greenery_type, greenery_score = detect_greenery_from_text(page_text)

    price_total = record_data.get("price")
    size_sqm = record_data.get("size_sqm")
    price_per_sqm = None
    if price_total and size_sqm and size_sqm > 0:
        price_per_sqm = round(price_total / size_sqm, 2)

    location = record_data.get("location", "")

    return PropertyRecord(
        property_id=generate_property_id("ceskereality", url),
        source="ceskereality",
        url=url,
        property_type=record_data.get("property_type", ""),
        size_category=record_data.get("size_category", ""),
        size_sqm=size_sqm,
        garden_present=greenery_type in ("private_garden", "shared_garden"),
        greenery_score=greenery_score,
        greenery_source=f"keyword:{greenery_type}" if greenery_score > 0 else "",
        price_total=price_total,
        price_per_sqm=price_per_sqm,
        location=location,
        district=_extract_district(location),
        lat=record_data.get("lat"),
        lon=record_data.get("lon"),
        energy_efficiency_rating=record_data.get("energy_rating", ""),
        scrape_date=today,
        last_updated=now,
        enrichment_status="done",
    )


def enrich_from_email(
    email_listings: list[dict],
    session: requests.Session,
    delay_range: tuple[float, float] = (2.0, 4.0),
) -> list[PropertyRecord]:
    """Take parsed email listings, enrich via page visit, return PropertyRecords."""
    records = []

    for item in email_listings:
        url = item["url"]
        record = enrich_from_page(url, session)

        if record:
            records.append(record)
        else:
            records.append(_minimal_record_from_email(item))

        time.sleep(random.uniform(*delay_range))

    return records


def _parse_jsonld(soup: BeautifulSoup) -> Optional[dict]:
    """Extract listing data from JSON-LD structured data."""
    import json

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        # Handle both single object and list
        items = data if isinstance(data, list) else [data]
        for item in items:
            if item.get("@type") != "RealEstateListing":
                continue

            offer = item.get("offers", {})
            if isinstance(offer, list):
                offer = offer[0] if offer else {}

            geo = item.get("geo", {})
            name = item.get("name", "")

            return {
                "price": normalize_price(offer.get("price", "")),
                "location": item.get("address", ""),
                "size_category": parse_size_category(name),
                "size_sqm": parse_size_sqm(name) or _extract_area(item),
                "property_type": _infer_type(name),
                "lat": geo.get("latitude"),
                "lon": geo.get("longitude"),
                "energy_rating": "",
            }

    return None


def _parse_html(soup: BeautifulSoup) -> Optional[dict]:
    """Fallback HTML parsing for listing data."""
    title = soup.find("h1")
    if not title:
        return None

    name = title.get_text(strip=True)
    price = None
    price_el = soup.find(class_=re.compile(r"price|cena", re.I))
    if price_el:
        price = normalize_price(price_el.get_text())

    return {
        "price": price,
        "location": "",
        "size_category": parse_size_category(name),
        "size_sqm": parse_size_sqm(name),
        "property_type": _infer_type(name),
        "lat": None,
        "lon": None,
        "energy_rating": "",
    }


def _extract_area(item: dict) -> Optional[float]:
    """Extract area from JSON-LD floorSize or similar."""
    floor_size = item.get("floorSize", {})
    if isinstance(floor_size, dict):
        val = floor_size.get("value")
        if val:
            return float(val)
    return None


def _infer_type(name: str) -> str:
    """Infer property type from listing name."""
    name_lower = name.lower()
    if "dům" in name_lower or "dum" in name_lower or "rodinný" in name_lower:
        return "house"
    return "apartment"


def _extract_district(location: str) -> str:
    """Extract district from location string."""
    match = re.search(r"(Praha\s*\d+)", location)
    if match:
        return match.group(1)
    parts = [p.strip() for p in location.split(",")]
    if parts:
        return parts[-1].split("-")[0].strip()
    return ""


def _minimal_record_from_email(item: dict) -> PropertyRecord:
    """Create partial record when enrichment fails."""
    url = item.get("url", "")
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    name = item.get("name", "")

    return PropertyRecord(
        property_id=generate_property_id("ceskereality", url),
        source="ceskereality",
        url=url,
        size_category=parse_size_category(name) or "",
        size_sqm=parse_size_sqm(name),
        price_total=item.get("price"),
        location=item.get("context", ""),
        scrape_date=today,
        last_updated=now,
        enrichment_status="failed",
    )
