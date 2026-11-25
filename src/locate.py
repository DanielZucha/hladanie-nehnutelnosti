"""Fill GPS and train distance for email-sourced listings (iDNES gives neither).

Geocoding uses OSM Nominatim via geopy (1 request/s policy); station lookup
reuses the Overpass query in src.transit. Prague listings are skipped: the
train rule only applies outside Prague.
"""

import logging
from typing import Callable, Optional

from src.region_filter import is_prague
from src.storage import PropertyRecord
from src.transit import find_nearest_train_station

logger = logging.getLogger(__name__)

Geocode = Callable[[str], Optional[tuple[float, float]]]
StationLookup = Callable[[float, float], Optional[dict]]


class LocateError(RuntimeError):
    """A geocode or station lookup failed with an error (not a plain miss)."""


def nominatim_geocoder() -> Geocode:
    """Rate-limited Nominatim geocoder restricted to Czechia."""
    from geopy.extra.rate_limiter import RateLimiter
    from geopy.geocoders import Nominatim

    geocode = RateLimiter(
        Nominatim(user_agent="home-search-monitor", timeout=10).geocode,
        min_delay_seconds=1.1,
        max_retries=1,
        swallow_exceptions=False,  # a network error must not look like "address not found"
    )

    def _lookup(query: str) -> Optional[tuple[float, float]]:
        hit = geocode(query, country_codes="cz")
        return (hit.latitude, hit.longitude) if hit else None

    return _lookup


def locate_records(
    records: list[PropertyRecord],
    geocode: Optional[Geocode] = None,
    nearest_station: Optional[StationLookup] = find_nearest_train_station,
) -> list[PropertyRecord]:
    """Geocode non-Prague records lacking GPS, then measure distance to the nearest station.

    Raises LocateError after the loop if any lookup errored, so callers can retry later.
    """
    failed = []
    for rec in records:
        if is_prague(rec.location, rec.district):
            continue
        try:
            if rec.lat is None or rec.lon is None:
                geocode = geocode or nominatim_geocoder()
                hit = geocode(f"{rec.location}, Česko")
                if hit:
                    rec.lat, rec.lon = hit
            if rec.lat is not None and rec.distance_to_train_km is None:
                station = nearest_station(rec.lat, rec.lon)
                if station:
                    rec.distance_to_train_km = station["distance_km"]
        except Exception:  # finish the batch first, then report
            logger.warning("Locate failed for %s", rec.url)
            failed.append(rec.url)
    if failed:
        raise LocateError(f"{len(failed)} lookups failed")
    return records
