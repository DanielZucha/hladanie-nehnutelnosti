"""Region filter: drop listings outside configured target regions.

Target: Praha + okres Praha-východ, Kolín, Nymburk.

Two-tier check:
1. Text match on location/district against Praha + target okres names
2. GPS bounding box fallback for listings with coordinates
"""

import logging
import re
from typing import Optional

from src.storage import PropertyRecord

logger = logging.getLogger(__name__)

# Target districts (okres) east of Prague
TARGET_DISTRICTS = [
    "Praha-východ",
    "Kolín",
    "Nymburk",
]

_PRAHA_RE = re.compile(r"Praha", re.IGNORECASE)
_OKRES_PATTERNS = [
    re.compile(rf"\b{re.escape(d)}\b", re.IGNORECASE)
    for d in TARGET_DISTRICTS
]

# Bounding box covering Praha + Praha-východ + Kolín + Nymburk.
# Slightly generous to catch border towns.
#   South: ~49.85 (south edge of Praha-východ, near Říčany/Kostelec)
#   North: ~50.35 (north edge of Nymburk district)
#   West:  ~14.20 (west edge of Prague)
#   East:  ~15.35 (east edge of Kolín district)
_BBOX_LAT_MIN = 49.85
_BBOX_LAT_MAX = 50.35
_BBOX_LON_MIN = 14.20
_BBOX_LON_MAX = 15.35


def _text_matches_region(location: str, district: str) -> bool:
    """Check location/district strings against known region names."""
    combined = f"{location} {district}"

    if _PRAHA_RE.search(combined):
        return True

    for pat in _OKRES_PATTERNS:
        if pat.search(combined):
            return True

    return False


def _gps_in_bbox(lat: Optional[float], lon: Optional[float]) -> bool:
    """Check if GPS coordinates fall within the target bounding box."""
    if lat is None or lon is None:
        return False
    return (
        _BBOX_LAT_MIN <= lat <= _BBOX_LAT_MAX
        and _BBOX_LON_MIN <= lon <= _BBOX_LON_MAX
    )


def is_in_target_region(
    location: str,
    district: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> bool:
    """Return True if the listing is in Praha, Praha-východ, Kolín, or Nymburk.

    Uses text matching first, then falls back to GPS bounding box.
    """
    if _text_matches_region(location, district):
        return True

    return _gps_in_bbox(lat, lon)


def filter_records(records: list[PropertyRecord]) -> list[PropertyRecord]:
    """Keep only records inside target regions. Logs dropped count."""
    kept = []
    dropped = 0

    for rec in records:
        if is_in_target_region(rec.location, rec.district, rec.lat, rec.lon):
            kept.append(rec)
        else:
            logger.info(
                "Filtered out (outside region): %s -- %s, %s",
                rec.url, rec.location, rec.district,
            )
            dropped += 1

    if dropped:
        logger.info("Region filter: kept %d, dropped %d", len(kept), dropped)

    return kept
