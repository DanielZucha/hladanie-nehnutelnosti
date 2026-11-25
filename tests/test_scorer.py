"""Tests for scoring without greenery and the categorized report picks."""

import pandas as pd
from src.config import ScoringConfig
from src.scorer import identify_categorized_picks, score_dataframe

CFG = ScoringConfig(
    weights={"price_attractiveness": 0.45, "transit": 0.25, "room_count": 0.15, "property_type": 0.15},
    room_scores={"3+kk": 60, "4+kk": 100},
    type_scores={"house_any": 100, "apartment_with_garden": 70, "apartment_without_garden": 40},
)


def _df(rows: list[dict]) -> pd.DataFrame:
    """Rows as the master CSV stores them (all strings)."""
    base = {
        "property_id": "x", "property_type": "apartment", "size_category": "3+kk",
        "size_sqm": "85", "price_total": "9000000", "price_per_sqm": "105882",
        "location": "Praha 8", "district": "Praha 8", "distance_to_train_km": "",
        "garden_present": "",
    }
    return pd.DataFrame([{**base, **r} for r in rows], columns=list(base), dtype=str)


def test_empty():
    out = score_dataframe(_df([]), CFG)
    assert out.empty


def test_one_record_scores():
    out = score_dataframe(_df([{}]), CFG)
    assert out.loc[0, "transit_dim"] == 100
    assert "greenery_dim" not in out.columns
    assert 0 < out.loc[0, "composite_score"] <= 100


def test_praha_vychod_is_not_prague_transit():
    out = score_dataframe(_df([{"location": "Úvaly", "district": "Praha-východ", "distance_to_train_km": "3.5"}]), CFG)
    assert out.loc[0, "transit_dim"] == 50


def test_na_price_scores_neutral():
    out = score_dataframe(_df([{"price_total": "", "price_per_sqm": ""}]), CFG)
    assert out.loc[0, "price_score"] is None or pd.isna(out.loc[0, "price_score"])
    assert out.loc[0, "composite_score"] > 0


def test_picks_largest_category():
    rows = [{"property_id": str(i), "size_sqm": str(80 + i), "price_per_sqm": str(100000 + i * 1000)} for i in range(20)]
    picks = identify_categorized_picks(score_dataframe(_df(rows), CFG), top_n=10, per_category=5)
    assert set(picks) == {"top_composite", "best_value", "largest"}
    taken = set(picks["top_composite"]["property_id"]) | set(picks["best_value"]["property_id"])
    largest = picks["largest"]
    assert not taken & set(largest["property_id"])
    sizes = pd.to_numeric(largest["size_sqm"]).tolist()
    assert sizes == sorted(sizes, reverse=True)


def test_picks_empty():
    picks = identify_categorized_picks(score_dataframe(_df([]), CFG))
    assert all(p.empty for p in picks.values())


def test_picks_skip_same_property_listed_twice():
    rows = [{"property_id": "a"}, {"property_id": "b"}, {"property_id": "c", "size_sqm": "120"}]
    picks = identify_categorized_picks(score_dataframe(_df(rows), CFG), top_n=10)
    assert len(picks["top_composite"]) == 2
