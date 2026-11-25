"""Ceskereality.cz parser: listings come from the alert email card only.

The detail pages sit behind a bot wall, so there is no page enrichment; the card gives
town, type, disposition, area and often wall type, but no price.
"""

import re
from datetime import date, datetime
from typing import Optional
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from src.parsers._shared import parse_size_category, parse_size_sqm
from src.storage import PropertyRecord, generate_property_id

DETAIL_URL = "https://www.ceskereality.cz/nemovitost.html?nid={nid}"


def _nid(href: str) -> Optional[str]:
    """Listing id from a nemovitost.html link, or None for other links."""
    parsed = urlparse(href)
    if "ceskereality.cz" not in parsed.netloc or not parsed.path.endswith("/nemovitost.html"):
        return None
    return (parse_qs(parsed.query).get("nid") or [None])[0]


def parse_email_html(html: str) -> list[dict]:
    """Extract one item per listing (url, name, description) from a ceskereality alert email."""
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    items: dict[str, dict] = {}
    for link in soup.find_all("a", href=True):
        nid = _nid(link["href"])
        name = link.get_text(" ", strip=True)
        # Each card links three times (image, title, "Více informací"); only the title names it.
        if not nid or not name or name.startswith("Více"):
            continue
        cell = link.find_parent("td")
        desc = cell.find("p", class_="hide-on-mobile") if cell else None
        items.setdefault(nid, {
            "url": DETAIL_URL.format(nid=nid),
            "name": name,
            "description": desc.get_text("", strip=True) if desc else "",
        })
    return list(items.values())


def _infer_type(title: str) -> str:
    """apartment / house from the card title; "" for land and others, which the rules drop."""
    t = title.lower()
    if "prodej bytu" in t:
        return "apartment"
    if re.search(r"\b(dom[uů]|vil[ay]|chalup)", t):
        return "house"
    return ""


def _building_type(desc: str) -> str:
    """Wall type keyword from the free-text description, "" when not mentioned."""
    d = desc.lower()
    if "panel" in d:
        return "panelová"
    if "cihl" in d:
        return "cihlová"
    return ""


def email_listings_to_records(email_listings: list[dict]) -> list[PropertyRecord]:
    """Convert parsed card items to PropertyRecords; locate.py adds GPS and train distance."""
    now, today = datetime.now().isoformat(), date.today().isoformat()
    records = []
    for item in email_listings:
        title, desc = item["name"], item["description"]
        location = title.split(" - Prodej")[0].strip()
        district = re.search(r"Praha\s*\d+", location)
        records.append(PropertyRecord(
            property_id=generate_property_id("ceskereality", item["url"]),
            source="ceskereality",
            url=item["url"],
            property_type=_infer_type(title),
            size_category=parse_size_category(title) or "",
            size_sqm=parse_size_sqm(desc),
            location=location,
            district=district.group(0) if district else "",
            building_type=_building_type(desc),
            scrape_date=today,
            last_updated=now,
            enrichment_status="email_only",
        ))
    return records
