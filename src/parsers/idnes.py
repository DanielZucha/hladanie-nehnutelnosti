"""Reality.idnes.cz parser: Visidoo/third-party alert emails + page enrichment."""

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


def parse_email_html(html: str) -> list[dict]:
    """Extract listing links from a Visidoo or third-party alert email.

    These emails typically contain links to reality.idnes.cz listing pages.
    The exact format depends on the alert service used.
    """
    soup = BeautifulSoup(html, "lxml")
    listings = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "reality.idnes.cz" not in href and "idnes.cz/detail" not in href:
            continue

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
    """Fetch an idnes reality listing page and extract data.

    Note: reality.idnes.cz is server-rendered ASP.NET, so requests + BS4 works.
    The site uses BotStopper, so enrichment may fail on some requests.
    """
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Failed to fetch idnes page: %s", url)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    now = datetime.now().isoformat()
    today = date.today().isoformat()

    title = soup.find("h1") or soup.find(class_=re.compile(r"title|nadpis", re.I))
    name = title.get_text(strip=True) if title else ""

    # Price extraction
    price = None
    price_el = soup.find(class_=re.compile(r"price|cena", re.I))
    if price_el:
        price = normalize_price(price_el.get_text())

    # Location
    location = ""
    loc_el = soup.find(class_=re.compile(r"location|lokalita|adresa", re.I))
    if loc_el:
        location = loc_el.get_text(strip=True)

    # Full page text for greenery detection
    page_text = soup.get_text(separator=" ")
    greenery_type, greenery_score = detect_greenery_from_text(page_text)

    size_sqm = parse_size_sqm(name) or parse_size_sqm(page_text[:500])
    size_cat = parse_size_category(name)

    price_per_sqm = None
    if price and size_sqm and size_sqm > 0:
        price_per_sqm = round(price / size_sqm, 2)

    return PropertyRecord(
        property_id=generate_property_id("idnes", url),
        source="idnes",
        url=url,
        property_type=_infer_type(name),
        size_category=size_cat or "",
        size_sqm=size_sqm,
        garden_present=greenery_type in ("private_garden", "shared_garden"),
        greenery_score=greenery_score,
        greenery_source=f"keyword:{greenery_type}" if greenery_score > 0 else "",
        price_total=price,
        price_per_sqm=price_per_sqm,
        location=location,
        district=_extract_district(location),
        energy_efficiency_rating="",
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


def _infer_type(name: str) -> str:
    name_lower = name.lower()
    if "dům" in name_lower or "dum" in name_lower or "rodinný" in name_lower:
        return "house"
    return "apartment"


def _extract_district(location: str) -> str:
    match = re.search(r"(Praha\s*\d+)", location)
    if match:
        return match.group(1)
    parts = [p.strip() for p in location.split(",")]
    if parts:
        return parts[-1].split("-")[0].strip()
    return ""


def _minimal_record_from_email(item: dict) -> PropertyRecord:
    url = item.get("url", "")
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    name = item.get("name", "")

    return PropertyRecord(
        property_id=generate_property_id("idnes", url),
        source="idnes",
        url=url,
        size_category=parse_size_category(name) or "",
        size_sqm=parse_size_sqm(name),
        price_total=item.get("price"),
        location=item.get("context", ""),
        scrape_date=today,
        last_updated=now,
        enrichment_status="failed",
    )
