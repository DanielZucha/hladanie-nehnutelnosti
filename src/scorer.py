"""Weighted property scoring with outlier detection.

Scoring dimensions:
- Price attractiveness (35%): inverse price/sqm + absolute cap penalty
- Greenery (25%): combined keyword + OSM score
- Transit (20%): Prague=100, else by train proximity
- Room count (10%): 4+ preferred
- Property type (10%): house > apt with garden > apt without

Null handling: if a dimension is null, redistribute its weight proportionally.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from src.config import ScoringConfig

logger = logging.getLogger(__name__)


def score_price_attractiveness(
    price_total: Optional[int],
    price_per_sqm: Optional[float],
    property_type: str,
    all_prices_per_sqm: pd.Series,
    config: ScoringConfig,
) -> Optional[float]:
    """Score price on 0-100 scale.

    Two components:
    1. Inverse price/sqm relative to dataset (lower = better)
    2. Absolute cap penalty: -10 per 1M above 12M CZK

    Houses get a tolerance: their effective price is reduced by
    house_premium_tolerance (25%) before comparison.
    """
    if price_per_sqm is None or price_total is None:
        return None

    effective_price_per_sqm = price_per_sqm
    effective_price_total = price_total

    # Houses: reduce effective price for scoring (we accept 25% premium)
    if property_type == "house":
        effective_price_per_sqm *= (1 - config.house_premium_tolerance)
        effective_price_total = int(effective_price_total * (1 - config.house_premium_tolerance))

    # Min-max inverse normalize price/sqm
    valid = all_prices_per_sqm.dropna()
    if valid.empty or valid.nunique() < 2:
        relative_score = 50.0
    else:
        min_p = valid.min()
        max_p = valid.max()
        # Inverse: lower price = higher score
        if max_p == min_p:
            relative_score = 50.0
        else:
            relative_score = (1 - (effective_price_per_sqm - min_p) / (max_p - min_p)) * 100
            relative_score = max(0.0, min(100.0, relative_score))

    # Absolute cap penalty
    cap = config.price_cap_czk
    penalty_rate = config.price_penalty_per_million_over
    if effective_price_total > cap:
        over = effective_price_total - cap
        millions_over = over / 1_000_000
        penalty = millions_over * penalty_rate
        relative_score = max(0.0, relative_score - penalty)

    return round(relative_score, 1)


def score_room_count(size_category: str, config: ScoringConfig) -> Optional[float]:
    """Score room count from config lookup."""
    if not size_category:
        return None
    return float(config.room_scores.get(size_category, 50))


def score_property_type(
    property_type: str,
    garden_present: Optional[bool],
    config: ScoringConfig,
) -> float:
    """Score property type: house > apartment with garden > apartment without."""
    if property_type == "house":
        return float(config.type_scores.get("house_any", 100))
    if garden_present:
        return float(config.type_scores.get("apartment_with_garden", 70))
    return float(config.type_scores.get("apartment_without_garden", 40))


def compute_composite_score(
    dimension_scores: dict[str, Optional[float]],
    weights: dict[str, float],
) -> float:
    """Compute weighted composite score, redistributing null weights.

    If a dimension is None, its weight is redistributed proportionally
    to dimensions that have values.
    """
    available = {k: v for k, v in dimension_scores.items() if v is not None}

    if not available:
        return 0.0

    total_weight = sum(weights.get(k, 0) for k in available)
    if total_weight == 0:
        return 0.0

    score = 0.0
    for dim, val in available.items():
        w = weights.get(dim, 0)
        normalized_w = w / total_weight  # redistribute
        score += val * normalized_w

    return round(score, 1)


def score_dataframe(df: pd.DataFrame, config: ScoringConfig) -> pd.DataFrame:
    """Score all properties in the DataFrame.

    Adds/updates columns: price_score, greenery_dim, transit_dim,
    room_dim, type_dim, composite_score.
    """
    df = df.copy()

    # Convert numeric columns
    for col in ["price_total", "price_per_sqm", "greenery_score", "size_sqm"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["garden_present"] = df["garden_present"].map(
        {"True": True, "False": False, "": None, True: True, False: False}
    )

    all_prices_per_sqm = df["price_per_sqm"].dropna()

    scores = []
    for _, row in df.iterrows():
        price_s = score_price_attractiveness(
            price_total=row["price_total"] if pd.notna(row["price_total"]) else None,
            price_per_sqm=row["price_per_sqm"] if pd.notna(row["price_per_sqm"]) else None,
            property_type=str(row.get("property_type", "")),
            all_prices_per_sqm=all_prices_per_sqm,
            config=config,
        )

        greenery_s = row["greenery_score"] if pd.notna(row.get("greenery_score")) else None

        # Transit: use pre-computed score if available, else None
        transit_s = None
        dist_train = row.get("distance_to_train_km")
        district = str(row.get("district", ""))
        location = str(row.get("location", ""))
        if "praha" in f"{district} {location}".lower():
            transit_s = float(config.transit.prague_score)
        elif pd.notna(dist_train) and dist_train != "":
            dist = float(dist_train)
            if dist <= 3.0:
                transit_s = float(config.transit.train_within_3km)
            elif dist <= 6.0:
                transit_s = float(config.transit.train_within_6km)
            else:
                transit_s = float(config.transit.no_train)

        room_s = score_room_count(str(row.get("size_category", "")), config)

        type_s = score_property_type(
            str(row.get("property_type", "")),
            row.get("garden_present"),
            config,
        )

        dims = {
            "price_attractiveness": price_s,
            "greenery": greenery_s,
            "transit": transit_s,
            "room_count": room_s,
            "property_type": type_s,
        }

        composite = compute_composite_score(dims, config.weights)

        scores.append({
            "price_score": price_s,
            "greenery_dim": greenery_s,
            "transit_dim": transit_s,
            "room_dim": room_s,
            "type_dim": type_s,
            "composite_score": composite,
        })

    score_df = pd.DataFrame(scores)
    for col in score_df.columns:
        df[col] = score_df[col].values

    return df


def identify_outliers(df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """Identify top outlier properties by composite score.

    Returns top N properties sorted by composite score descending.
    """
    scored = df[pd.to_numeric(df["composite_score"], errors="coerce").notna()].copy()
    scored["composite_score"] = pd.to_numeric(scored["composite_score"])
    scored = scored.sort_values("composite_score", ascending=False)
    return scored.head(top_n)
