"""Greenery detection using OSM Overpass API for park/garden proximity.

Combines three signals:
1. Listing text keywords (from parsers/_shared.py, already applied during parsing)
2. Structured fields from portal data (already applied during parsing)
3. OSM Overpass query for nearby green spaces (this module)

The final greenery score is the MAX of keyword-based and OSM-based scores.
"""

import logging
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# OSM tags that indicate green/park/garden spaces
GREENERY_QUERY_TEMPLATE = """
[out:json][timeout:10];
(
  way["leisure"="park"](around:{radius},{lat},{lon});
  way["leisure"="garden"](around:{radius},{lat},{lon});
  way["landuse"="grass"](around:{radius},{lat},{lon});
  way["landuse"="recreation_ground"](around:{radius},{lat},{lon});
  relation["leisure"="park"](around:{radius},{lat},{lon});
  node["leisure"="playground"](around:{radius},{lat},{lon});
);
out count;
"""


@retry(stop=stop_after_attempt(2), wait=wait_fixed(3))
def count_nearby_green_spaces(
    lat: float,
    lon: float,
    radius_m: int = 300,
) -> int:
    """Query OSM Overpass for green spaces within radius of coordinates.

    Returns count of green space elements found.
    """
    query = GREENERY_QUERY_TEMPLATE.format(radius=radius_m, lat=lat, lon=lon)

    resp = requests.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    # Overpass "out count" returns total in elements
    elements = data.get("elements", [])
    if elements and "tags" in elements[0]:
        return int(elements[0]["tags"].get("total", 0))
    return len(elements)


def osm_greenery_score(
    lat: Optional[float],
    lon: Optional[float],
    radius_m: int = 300,
) -> tuple[int, str]:
    """Compute greenery score from OSM data.

    Returns (score, source_description).
    """
    if lat is None or lon is None:
        return 0, ""

    try:
        count = count_nearby_green_spaces(lat, lon, radius_m)
    except Exception:
        logger.warning("OSM query failed for %s, %s", lat, lon)
        return 0, ""

    if count >= 3:
        return 60, f"osm:park_within_{radius_m}m(count={count})"
    elif count >= 1:
        return 50, f"osm:park_within_{radius_m}m(count={count})"
    else:
        return 0, ""


def compute_final_greenery_score(
    keyword_score: float,
    keyword_source: str,
    lat: Optional[float],
    lon: Optional[float],
    osm_radius_m: int = 300,
) -> tuple[float, str]:
    """Combine keyword-based and OSM-based greenery scores.

    Takes the MAX of keyword and OSM scores.
    If both contribute, source reflects both.
    """
    osm_score, osm_source = osm_greenery_score(lat, lon, osm_radius_m)

    if keyword_score >= osm_score:
        final_score = keyword_score
        source = keyword_source
        if osm_score > 0:
            source = f"{keyword_source}+{osm_source}"
    else:
        final_score = osm_score
        source = osm_source
        if keyword_score > 0:
            source = f"{osm_source}+{keyword_source}"

    return final_score, source
