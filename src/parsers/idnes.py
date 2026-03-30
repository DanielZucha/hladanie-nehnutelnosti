"""Reality.idnes.cz parser: native 'hlidaci pes' email alerts + page enrichment.

iDNES alert emails contain rich listing data directly:
- Property type and size (e.g., "domu 235 m2 s pozemkem 878 m2")
- Price in CZK
- Location with district
- Direct detail links

Format: each listing is a table block with pattern:
  Prodej | domu X m2 s pozemkem Y m2 | PRICE Kč | Location, District | link
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
    normalize_price,
)
from src.storage import PropertyRecord, generate_property_id

logger = logging.getLogger(__name__)


def parse_email_html(html: str) -> list[dict]:
    """Extract listings from an iDNES hlidaci pes alert email.

    Returns list of dicts with: url, size_sqm, plot_sqm, price, location,
    property_type, size_text.
    """
    soup = BeautifulSoup(html, "lxml")
    listings = []

    # Find all links to detail pages
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "reality.idnes.cz/detail/" not in href:
            continue

        # The link is deeply nested. Walk up to find the table.row
        # that contains the full listing text (price, size, location).
        block = link.find_parent("table", class_="row")
        if not block:
            # Fallback: walk up through multiple parent tables
            for parent_table in link.parents:
                if parent_table.name == "table":
                    text = parent_table.get_text(strip=True)
                    if "Kč" in text and "m²" in text:
                        block = parent_table
                        break
        if not block:
            continue

        block_text = block.get_text(separator=" | ", strip=True)

        # Extract price: "9 499 000 Kč"
        price_match = re.search(r"([\d\s]+)\s*Kč", block_text)
        price = normalize_price(price_match.group(1)) if price_match else None

        # Extract size: "domu 235 m²" or "bytu 75 m²"
        size_match = re.search(r"(\d+)\s*m[²2]", block_text)
        size_sqm = float(size_match.group(1)) if size_match else None

        # Extract plot size: "s pozemkem 878 m²"
        plot_match = re.search(r"pozemkem\s+(\d+)\s*m[²2]", block_text)
        plot_sqm = float(plot_match.group(1)) if plot_match else None

        # Extract location: last meaningful segment before "Zobrazit"
        # Pattern: "Nová, Veltruby, okres Kolín"
        loc_match = re.search(
            r"Kč\s*\|\s*(.+?)\s*\|\s*Zobrazit", block_text
        )
        location = loc_match.group(1).strip() if loc_match else ""

        # Property type from URL or text
        if "/dum/" in href or "domu" in block_text.lower():
            prop_type = "house"
        elif "/byt/" in href or "bytu" in block_text.lower():
            prop_type = "apartment"
        else:
            prop_type = ""

        # Size category from text: "bytu 3+kk" or similar
        cat_match = re.search(r"(\d\+(?:kk|1|KK))", block_text, re.IGNORECASE)
        size_category = cat_match.group(1).lower() if cat_match else ""

        listings.append({
            "url": href.split("?")[0],  # strip UTM params
            "price": price,
            "size_sqm": size_sqm,
            "plot_sqm": plot_sqm,
            "location": location,
            "property_type": prop_type,
            "size_category": size_category,
            "block_text": block_text,
        })

    # Deduplicate by URL
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
) -> Optional[dict]:
    """Fetch an iDNES listing page for additional fields.

    Returns dict with extra fields or None on failure.
    """
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Failed to fetch idnes page: %s", url)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    page_text = soup.get_text(separator=" ")

    greenery_type, greenery_score = detect_greenery_from_text(page_text)

    # Try to extract energy rating
    energy = ""
    energy_match = re.search(r"PENB[:\s]*([A-G])", page_text, re.IGNORECASE)
    if energy_match:
        energy = energy_match.group(1).upper()

    return {
        "greenery_type": greenery_type,
        "greenery_score": greenery_score,
        "energy_rating": energy,
    }


def email_listings_to_records(
    email_listings: list[dict],
    session: requests.Session,
    delay_range: tuple[float, float] = (2.0, 4.0),
) -> list[PropertyRecord]:
    """Convert parsed email listings to PropertyRecords, enriching via page visit."""
    records = []
    now = datetime.now().isoformat()
    today = date.today().isoformat()

    for item in email_listings:
        url = item["url"]
        pid = generate_property_id("idnes", url)

        price = item.get("price")
        size_sqm = item.get("size_sqm")
        price_per_sqm = None
        if price and size_sqm and size_sqm > 0:
            price_per_sqm = round(price / size_sqm, 2)

        # Extract district from location like "Nová, Veltruby, okres Kolín"
        location = item.get("location", "")
        district = _extract_district(location)

        # Default greenery from email text
        greenery_type, greenery_score = detect_greenery_from_text(
            item.get("block_text", "")
        )

        # Houses with plot > 0 likely have a garden
        if item.get("property_type") == "house" and item.get("plot_sqm", 0):
            if greenery_score < 95:
                greenery_type = "private_garden"
                greenery_score = 95

        # Enrich from page visit
        enrichment_status = "done"
        energy_rating = ""
        extra = enrich_from_page(url, session)
        if extra:
            if extra["greenery_score"] > greenery_score:
                greenery_score = extra["greenery_score"]
                greenery_type = extra["greenery_type"]
            energy_rating = extra.get("energy_rating", "")
        else:
            enrichment_status = "partial"

        records.append(PropertyRecord(
            property_id=pid,
            source="idnes",
            url=url,
            property_type=item.get("property_type", ""),
            size_category=item.get("size_category", ""),
            size_sqm=size_sqm,
            garden_present=greenery_type in ("private_garden", "shared_garden"),
            greenery_score=greenery_score,
            greenery_source=f"keyword:{greenery_type}" if greenery_score > 0 else "",
            price_total=price,
            price_per_sqm=price_per_sqm,
            location=location,
            district=district,
            energy_efficiency_rating=energy_rating,
            scrape_date=today,
            last_updated=now,
            enrichment_status=enrichment_status,
        ))

        time.sleep(random.uniform(*delay_range))

    return records


def _extract_district(location: str) -> str:
    """Extract district from location like 'Nová, Veltruby, okres Kolín'."""
    match = re.search(r"(Praha\s*\d+)", location)
    if match:
        return match.group(1)
    # "okres X" pattern
    okres_match = re.search(r"okres\s+([^,]+)", location)
    if okres_match:
        return okres_match.group(1).strip()
    parts = [p.strip() for p in location.split(",")]
    if parts:
        return parts[-1].strip()
    return ""
