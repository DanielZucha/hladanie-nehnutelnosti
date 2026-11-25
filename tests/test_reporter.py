"""Smoke tests for the Czech report HTML and scatter plot."""

import pandas as pd

from src.reporter import generate_report_html, generate_scatter_plot

WEIGHTS = {"price_attractiveness": 0.45, "transit": 0.25, "room_count": 0.15, "property_type": 0.15}


def _picks(n: int) -> pd.DataFrame:
    return pd.DataFrame([{
        "property_id": str(i), "url": f"https://x/{i}", "property_type": "house",
        "size_category": "", "location": "Úvaly", "price_total": "9000000",
        "price_per_sqm": "90000", "size_sqm": str(100 + i), "composite_score": 80.0,
        "price_score": 60.0, "transit_dim": 100.0, "distance_to_train_km": "1.2",
    } for i in range(n)])


def test_sections_and_weights():
    html = generate_report_html(
        {"top_composite": _picks(2), "best_value": _picks(0), "largest": _picks(1)},
        total_count=3, new_count=2, weights=WEIGHTS,
    )
    assert "Největší plocha" in html
    assert "Cenově nejvýhodnější" not in html  # empty section is skipped
    assert "cenovou výhodnost (45 %)" in html
    assert "vlak 1.2 km" in html
    assert "zeleň" not in html.lower()


def test_scatter_png_with_na_sizes():
    df = _picks(3)
    df.loc[0, "size_sqm"] = ""
    png = generate_scatter_plot(df, {"1"})
    assert png[:4] == b"\x89PNG"


def test_scatter_empty():
    assert generate_scatter_plot(_picks(0).reindex(columns=_picks(1).columns), set())[:4] == b"\x89PNG"
