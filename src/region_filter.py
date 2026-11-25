"""Hard listing rules: every source passes through here before it reaches the master CSV.

Rules: Praha, or the east corridor up to Úvaly/Nehvizdy with a train station
within reach; apartment or house; 79+ m2; 3+kk or larger; 6-14M CZK; not panel.
"""

import logging
import re
from typing import Optional

from src.config import SearchConfig
from src.storage import PropertyRecord

logger = logging.getLogger(__name__)

# "Praha", "Praha 8", "Praha 9 - Kyje"; not "Praha-východ" / "okres Praha-západ".
_PRAHA_RE = re.compile(r"\bPraha(?![-\s]*(?:východ|západ))\b", re.IGNORECASE)
_ROOMS_RE = re.compile(r"^(\d+)\s*\+")


def is_prague(location: str, district: str) -> bool:
    """True for listings inside Prague city limits."""
    return bool(_PRAHA_RE.search(f"{district} | {location}"))


def _in_corridor(lat: Optional[float], lon: Optional[float], cfg: SearchConfig) -> bool:
    """True when coordinates fall inside the configured east corridor box."""
    if lat is None or lon is None:
        return False
    box = cfg.corridor
    return box.lat_min <= lat <= box.lat_max and box.lon_min <= lon <= box.lon_max


def _fail_reason(rec: PropertyRecord, cfg: SearchConfig) -> str:
    """Return why a record breaks a rule, or "" when it passes all of them."""
    if rec.property_type not in ("apartment", "house"):
        return "type"
    if rec.size_sqm is None or rec.size_sqm < cfg.min_area_sqm:
        return "area"
    rooms = _ROOMS_RE.match(rec.size_category or "")
    if rec.property_type == "apartment" and rooms and int(rooms.group(1)) < cfg.min_rooms:
        return "disposition"
    # Price on request (None) is kept: it cannot be judged, and the agent may still fit.
    if rec.price_total is not None and not (cfg.price_min_czk <= rec.price_total <= cfg.price_max_czk):
        return "price"
    if "panel" in (rec.building_type or "").lower():
        return "panel"
    if is_prague(rec.location, rec.district):
        return ""
    location = (rec.location or "").casefold()
    if any(town.casefold() in location for town in cfg.exclude_towns):
        return "excluded_town"
    if not _in_corridor(rec.lat, rec.lon, cfg):
        return "region"
    if rec.distance_to_train_km is None or rec.distance_to_train_km > cfg.train_max_km:
        return "train"
    return ""


def passes_rules(rec: PropertyRecord, cfg: SearchConfig) -> bool:
    """True when the record satisfies every hard rule."""
    return _fail_reason(rec, cfg) == ""


def filter_records(records: list[PropertyRecord], cfg: SearchConfig) -> list[PropertyRecord]:
    """Keep records passing all hard rules; log drop counts per rule."""
    kept, dropped = [], {}
    for rec in records:
        reason = _fail_reason(rec, cfg)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
        else:
            kept.append(rec)
    if dropped:
        logger.info("Rules: kept %d, dropped %s", len(kept), dropped)
    return kept
