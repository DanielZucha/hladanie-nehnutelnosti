"""Nearest railway station from a committed OSM snapshot (src/stations.json).

Public Overpass servers time out too often for per-listing lookups, and
stations in the corridor rarely change. Refresh the JSON when the corridor
box in config.yaml grows beyond the snapshot's bbox.
"""

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Optional

_STATIONS_PATH = Path(__file__).with_name("stations.json")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two GPS points in kilometers."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@lru_cache(maxsize=1)
def _load_stations() -> tuple[dict, ...]:
    """Read the station snapshot once per process."""
    return tuple(json.loads(_STATIONS_PATH.read_text(encoding="utf-8"))["stations"])


def find_nearest_train_station(
    lat: float,
    lon: float,
    stations: Optional[list[dict]] = None,
) -> Optional[dict]:
    """Return {name, lat, lon, distance_km} of the closest station, or None if the list is empty."""
    pool = _load_stations() if stations is None else stations
    best = min(pool, key=lambda s: haversine_km(lat, lon, s["lat"], s["lon"]), default=None)
    if best is None:
        return None
    return {**best, "distance_km": round(haversine_km(lat, lon, best["lat"], best["lon"]), 2)}
