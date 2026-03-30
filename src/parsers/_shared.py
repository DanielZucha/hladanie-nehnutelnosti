"""Shared utilities for email and listing page parsing."""

import re
from typing import Optional


# Czech keywords for greenery detection in listing descriptions
GARDEN_KEYWORDS = [
    "zahrada", "zahrádka", "zahrádkou", "zahradou", "zahradní",
    "předzahrádka", "předzahrádkou",
]

SHARED_GARDEN_KEYWORDS = [
    "společná zahrada", "komunitní zahrada", "sdílená zahrada",
]

TERRACE_KEYWORDS = [
    "terasa", "terasou", "balkon", "balkón", "balkonem", "lodžie", "lodžií",
]

PARK_KEYWORDS = [
    "park", "parku", "zeleň", "zeleni",
]


def detect_greenery_from_text(text: str) -> tuple[str, int]:
    """Detect greenery type from listing description text.

    Returns (greenery_type, score) where greenery_type is one of:
    private_garden, shared_garden, terrace_balcony, park_mention, none
    """
    text_lower = text.lower()

    for kw in GARDEN_KEYWORDS:
        if kw in text_lower:
            return "private_garden", 95

    for kw in SHARED_GARDEN_KEYWORDS:
        if kw in text_lower:
            return "shared_garden", 65

    for kw in PARK_KEYWORDS:
        # Avoid false positives: "parking", street names with "park"
        pattern = rf"\b{kw}\b"
        if re.search(pattern, text_lower):
            return "park_mention", 50

    for kw in TERRACE_KEYWORDS:
        if kw in text_lower:
            return "terrace_balcony", 25

    return "none", 0


def parse_size_category(name: str) -> Optional[str]:
    """Extract room layout from listing name like 'Prodej bytu 3+kk 75 m2'."""
    match = re.search(r"(\d\+(?:kk|1|KK))", name, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return None


def parse_size_sqm(name: str) -> Optional[float]:
    """Extract square meters from listing name."""
    match = re.search(r"(\d+)\s*m[²2]", name)
    if match:
        return float(match.group(1))
    return None


def room_count_from_category(category: str) -> int:
    """Extract numeric room count from size category like '3+kk' or '4+1'."""
    match = re.match(r"(\d+)", category)
    if match:
        return int(match.group(1))
    return 0


def normalize_price(price_raw: str | int | float) -> Optional[int]:
    """Normalize price to integer CZK."""
    if isinstance(price_raw, (int, float)):
        return int(price_raw)
    cleaned = re.sub(r"[^\d]", "", str(price_raw))
    if cleaned:
        return int(cleaned)
    return None
