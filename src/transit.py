"""Transit scoring: proximity to train stations with Prague service.

Scoring rules:
- Prague listings: 100 (automatic)
- Non-Prague: train station within 3km = 100, within 6km = 50, else = 0

Uses OSM Overpass API to find nearest railway station.
"""

import logging
import math
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Query for railway stations (not tram stops, not bus stops)
TRAIN_STATION_QUERY_TEMPLATE = """
[out:json][timeout:10];
(
  node["railway"="station"](around:{radius},{lat},{lon});
  node["railway"="halt"](around:{radius},{lat},{lon});
);
out body;
"""


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS points in kilometers."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


@retry(stop=stop_after_attempt(2), wait=wait_fixed(3))
def find_nearest_train_station(
    lat: float,
    lon: float,
    search_radius_m: int = 7000,
) -> Optional[dict]:
    """Find nearest train station within search radius.

    Returns dict with keys: name, lat, lon, distance_km or None if not found.
    """
    query = TRAIN_STATION_QUERY_TEMPLATE.format(
        radius=search_radius_m, lat=lat, lon=lon
    )

    resp = requests.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    elements = data.get("elements", [])
    if not elements:
        return None

    # Find the closest one
    closest = None
    min_dist = float("inf")

    for el in elements:
        el_lat = el.get("lat")
        el_lon = el.get("lon")
        if el_lat is None or el_lon is None:
            continue

        dist = haversine_km(lat, lon, el_lat, el_lon)
        if dist < min_dist:
            min_dist = dist
            closest = {
                "name": el.get("tags", {}).get("name", "unknown"),
                "lat": el_lat,
                "lon": el_lon,
                "distance_km": round(dist, 2),
            }

    return closest


def is_prague_location(district: str, location: str) -> bool:
    """Check if a listing is in Prague based on district/location strings."""
    combined = f"{district} {location}".lower()
    return "praha" in combined or "prague" in combined


def compute_transit_score(
    lat: Optional[float],
    lon: Optional[float],
    district: str,
    location: str,
) -> tuple[int, Optional[float]]:
    """Compute transit score and distance to nearest train station.

    Returns (score, distance_to_train_km).
    """
    if is_prague_location(district, location):
        return 100, 0.0

    if lat is None or lon is None:
        # Can't compute without GPS, give neutral score
        return 50, None

    try:
        station = find_nearest_train_station(lat, lon)
    except Exception:
        logger.warning("Train station lookup failed for %s, %s", lat, lon)
        return 50, None

    if station is None:
        return 0, None

    dist = station["distance_km"]
    logger.info(
        "Nearest station to (%.4f, %.4f): %s at %.1f km",
        lat, lon, station["name"], dist,
    )

    if dist <= 3.0:
        return 100, dist
    elif dist <= 6.0:
        return 50, dist
    else:
        return 0, dist
