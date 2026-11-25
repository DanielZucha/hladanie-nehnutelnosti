"""Sreality.cz v1 search API client (the v2 /estates API returns 404 since mid-2026).

robots.txt disallows bots; we keep volume low (one filtered query per type, ~10
requests per run) and raise SrealityBlockedError on any non-JSON answer so a
block shows up as a failed GitHub run instead of an empty report.
"""

import logging
import random
import time
from datetime import date, datetime

import requests

from src.config import SearchConfig
from src.parsers._shared import normalize_price, parse_size_category, parse_size_sqm
from src.storage import PropertyRecord, generate_property_id

logger = logging.getLogger(__name__)

SREALITY_API_BASE = "https://www.sreality.cz/api/v1"
SREALITY_WEB_BASE = "https://www.sreality.cz"

_MAIN_CB = {"apartment": 1, "house": 2}
_MAIN_SEO = {1: "byt", 2: "dum"}
_HOUSE_SUB_SEO = {37: "rodinny", 39: "vila", 54: "vicegeneracni"}


class SrealityBlockedError(RuntimeError):
    """Sreality answered with an error status or non-JSON (block, captcha, API change)."""


def search_params(cfg: SearchConfig, property_type: str) -> dict:
    """Server-side filter params for one property type."""
    return {
        "category_type_cb": 1,  # sale
        "category_main_cb": _MAIN_CB[property_type],
        "category_sub_cb": cfg.apartment_sub_cbs if property_type == "apartment" else cfg.house_sub_cbs,
        "locality_region_id": cfg.sreality_region_id,
        "locality_district_id": cfg.sreality_district_ids,
        "building_type": cfg.building_types,
        "price_from": cfg.price_min_czk,
        "price_to": cfg.price_max_czk,
        "usable_area_from": int(cfg.min_area_sqm),
    }


def fetch_all(
    session: requests.Session,
    params: dict,
    limit: int = 100,
    delay_range: tuple[float, float] = (1.0, 2.0),
) -> list[dict]:
    """Page through /estates/search with offset/limit; raise on block."""
    results: list[dict] = []
    offset = 0
    while True:
        resp = session.get(
            f"{SREALITY_API_BASE}/estates/search",
            params={**params, "limit": limit, "offset": offset},
            timeout=30,
        )
        try:
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}")
            data = resp.json()
            page = data["results"]
            total = data["pagination"]["total"]
            if not isinstance(page, list) or not isinstance(total, int):
                raise ValueError("unexpected payload shape")
        except (ValueError, KeyError, TypeError) as exc:
            raise SrealityBlockedError(f"sreality search failed at offset {offset}: {exc}") from exc

        results.extend(page)
        if not page or len(results) >= total:
            return results
        offset += len(page)
        time.sleep(random.uniform(*delay_range))


def _location(loc: dict) -> str:
    """'Street, Citypart, City' without empty or repeated parts."""
    parts: list[str] = []
    for key in ("street", "citypart", "city"):
        val = (loc.get(key) or "").strip()
        if val and val not in parts:
            parts.append(val)
    return ", ".join(parts)


def _detail_url(estate: dict, main_cb: int) -> str:
    """Detail URL; sreality redirects any locality slug to the canonical one."""
    sub = estate.get("category_sub_cb") or {}
    sub_seo = sub.get("name", "x") if main_cb == 1 else _HOUSE_SUB_SEO.get(sub.get("value"), "x")
    loc = estate.get("locality") or {}
    slug = "-".join(s for s in (loc.get("city_seo_name"), loc.get("citypart_seo_name")) if s) or "x"
    return f"{SREALITY_WEB_BASE}/detail/prodej/{_MAIN_SEO.get(main_cb, 'byt')}/{sub_seo}/{slug}/{estate.get('hash_id')}"


def estate_to_record(estate: dict) -> PropertyRecord:
    """Convert one v1 search result to a PropertyRecord."""
    main_cb = (estate.get("category_main_cb") or {}).get("value", 1)
    name = estate.get("advert_name", "")
    loc = estate.get("locality") or {}
    url = _detail_url(estate, main_cb)

    price = normalize_price(estate.get("price_czk") or 0)
    if price is not None and price <= 1:
        price = None  # sreality uses 0/1 for "price on request"
    size_sqm = parse_size_sqm(name)
    train_m = estate.get("poi_train_distance")

    return PropertyRecord(
        property_id=generate_property_id("sreality", str(estate.get("hash_id"))),  # URL slug can change
        source="sreality",
        url=url,
        property_type="house" if main_cb == 2 else "apartment",
        size_category=(parse_size_category(name) or "") if main_cb == 1 else "",
        size_sqm=size_sqm,
        price_total=price,
        price_per_sqm=round(price / size_sqm, 2) if price and size_sqm else None,
        location=_location(loc),
        district=loc.get("district", ""),
        lat=loc.get("gps_lat"),
        lon=loc.get("gps_lon"),
        distance_to_train_km=round(train_m / 1000, 3) if train_m is not None else None,
        scrape_date=date.today().isoformat(),
        last_updated=datetime.now().isoformat(),
        enrichment_status="done",
    )


def scrape_listings(
    session: requests.Session,
    cfg: SearchConfig,
    delay_range: tuple[float, float] = (1.0, 2.0),
) -> list[PropertyRecord]:
    """Fetch the full filtered inventory (apartments + houses), deduplicated by hash_id."""
    seen: set = set()
    records: list[PropertyRecord] = []
    for ptype in ("apartment", "house"):
        estates = fetch_all(session, search_params(cfg, ptype), delay_range=delay_range)
        logger.info("sreality %s: %d listings", ptype, len(estates))
        if ptype == "apartment" and not estates:
            raise SrealityBlockedError("0 apartments in Praha: filter params likely broken")
        for est in estates:
            if est.get("hash_id") in seen:
                continue
            seen.add(est.get("hash_id"))
            records.append(estate_to_record(est))
    return records
