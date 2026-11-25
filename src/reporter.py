"""Report generation: HTML email with outlier table and scatter plot. Czech language."""

import io
import logging
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Czech month names for date formatting
_CZ_MONTHS = {
    1: "ledna", 2: "února", 3: "března", 4: "dubna",
    5: "května", 6: "června", 7: "července", 8: "srpna",
    9: "září", 10: "října", 11: "listopadu", 12: "prosince",
}

# Czech property type labels
_CZ_TYPES = {
    "house": "dům",
    "apartment": "byt",
}


def _cz_date(dt: datetime) -> str:
    """Format date in Czech: '30. března 2026'."""
    return f"{dt.day}. {_CZ_MONTHS[dt.month]} {dt.year}"


def generate_scatter_plot(df: pd.DataFrame, outlier_ids: set[str]) -> bytes:
    """Generate 2D scatter plot: X=price efficiency, Y=usable area.

    Returns PNG bytes.
    """
    plot_df = df.copy()
    plot_df["composite_score"] = pd.to_numeric(plot_df["composite_score"], errors="coerce")
    plot_df["price_score"] = pd.to_numeric(plot_df.get("price_score", 0), errors="coerce")
    plot_df["size_sqm"] = pd.to_numeric(plot_df["size_sqm"], errors="coerce")
    plot_df = plot_df.dropna(subset=["price_score"])

    x = plot_df["price_score"].fillna(0)
    y = plot_df["size_sqm"].fillna(0)

    is_outlier = plot_df["property_id"].isin(outlier_ids)

    fig, ax = plt.subplots(figsize=(10, 7))

    ax.scatter(
        x[~is_outlier], y[~is_outlier],
        c="#94a3b8", alpha=0.5, s=30, label="Ostatní nabídky",
    )
    ax.scatter(
        x[is_outlier], y[is_outlier],
        c="#ef4444", alpha=0.9, s=80, label="Top výběr", zorder=5,
    )

    ax.set_xlabel("Cenová výhodnost (vyšší = levnější za m²)", fontsize=11)
    ax.set_ylabel("Užitná plocha (m²)", fontsize=11)
    ax.set_title("Přehled nemovitostí", fontsize=13)
    ax.legend(loc="upper left")
    ax.set_xlim(-5, 105)
    ax.grid(True, alpha=0.3)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generate_report_html(
    categorized_picks: dict[str, "pd.DataFrame"],
    total_count: int,
    new_count: int,
    weights: dict[str, float],
    scatter_cid: str = "scatter_plot",
) -> str:
    """Generate HTML report email body in Czech with categorized picks."""
    now = _cz_date(datetime.now())

    _SECTION_CONFIG = {
        "top_composite": {
            "title": "Nejlepší celkové skóre",
            "color": "#ef4444",
            "description": "Nejlepší kombinace ceny, dostupnosti, dispozice a typu nemovitosti.",
        },
        "best_value": {
            "title": "Cenově nejvýhodnější",
            "color": "#22c55e",
            "description": "Nejnižší cena za m² -- stojí za prozkoumání.",
        },
        "largest": {
            "title": "Největší plocha",
            "color": "#3b82f6",
            "description": "Nejvíc metrů čtverečních v cenovém rozpětí.",
        },
    }

    sections_html = ""
    for category_key, section in _SECTION_CONFIG.items():
        picks = categorized_picks.get(category_key)
        if picks is None or picks.empty:
            continue

        rows_html = ""
        for i, (_, row) in enumerate(picks.iterrows(), 1):
            price_fmt = _format_price(row.get("price_total"))
            ppsqm_fmt = _format_price(row.get("price_per_sqm"))
            score = row.get("composite_score", "?")
            comment = _generate_comment(row)
            prop_type = _CZ_TYPES.get(str(row.get("property_type", "")), "?")
            size_cat = row.get("size_category", "")
            label = f"{prop_type} {size_cat}".strip()

            rows_html += f"""
            <tr style="border-bottom: 1px solid #e2e8f0;">
                <td style="padding: 8px;">{i}</td>
                <td style="padding: 8px;">
                    <a href="{row.get('url', '#')}" style="color: #2563eb;">
                        {label}
                    </a>
                </td>
                <td style="padding: 8px;">{row.get('location', '?')}</td>
                <td style="padding: 8px;">{price_fmt}</td>
                <td style="padding: 8px;">{ppsqm_fmt}/m²</td>
                <td style="padding: 8px;">{row.get('size_sqm', '?')} m²</td>
                <td style="padding: 8px; font-weight: bold;">{score}</td>
                <td style="padding: 8px; font-size: 0.9em; color: #475569;">{comment}</td>
            </tr>
            """

        sections_html += f"""
        <h2 style="color: #334155; margin-top: 25px;">
            <span style="color: {section['color']};">&#9679;</span> {section['title']}
        </h2>
        <p style="color: #64748b; font-size: 0.9em; margin-top: -8px;">{section['description']}</p>

        <table style="width: 100%; border-collapse: collapse; font-size: 0.95em;">
            <thead>
                <tr style="background: #f8fafc; border-bottom: 2px solid #cbd5e1;">
                    <th style="padding: 8px; text-align: left;">#</th>
                    <th style="padding: 8px; text-align: left;">Nemovitost</th>
                    <th style="padding: 8px; text-align: left;">Lokalita</th>
                    <th style="padding: 8px; text-align: left;">Cena</th>
                    <th style="padding: 8px; text-align: left;">Kč/m²</th>
                    <th style="padding: 8px; text-align: left;">Plocha</th>
                    <th style="padding: 8px; text-align: left;">Skóre</th>
                    <th style="padding: 8px; text-align: left;">Proč</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        """

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px;">
        <h1 style="color: #1e293b;">Přehled nemovitostí -- {now}</h1>

        <div style="background: #f1f5f9; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px;">
            <strong>{total_count}</strong> sledovaných nabídek celkem |
            <strong>{new_count}</strong> nových od posledního reportu
        </div>

        {sections_html}

        <h2 style="color: #334155; margin-top: 30px;">Přehled trhu</h2>
        <img src="cid:{scatter_cid}" style="width: 100%; max-width: 700px;" alt="Graf nemovitostí" />

        <div style="background: #f1f5f9; padding: 12px 16px; border-radius: 8px; margin-top: 30px;">
            <a href="https://drive.google.com/drive/folders/1YfuWoaifh662Qd1YdIUO6NBGO0GZqgKt" style="color: #2563eb; font-weight: bold;">
                Všechny nabídky na Google Drive
            </a>
        </div>

        <hr style="margin-top: 20px; border: none; border-top: 1px solid #e2e8f0;" />
        <p style="font-size: 0.85em; color: #94a3b8;">
            Generováno automaticky. Skóre zohledňuje: {_weights_text(weights)}.
        </p>
    </body>
    </html>
    """
    return html


_CZ_WEIGHTS = {
    "price_attractiveness": "cenovou výhodnost",
    "transit": "dopravní dostupnost do Prahy",
    "room_count": "počet pokojů",
    "property_type": "typ nemovitosti",
}


def _weights_text(weights: dict[str, float]) -> str:
    """'cenovou výhodnost (45 %), ...' from the configured weights."""
    return ", ".join(f"{_CZ_WEIGHTS.get(k, k)} ({round(v * 100)} %)" for k, v in weights.items())


def _format_price(val) -> str:
    """Format price as '5 900 000 Kč'."""
    try:
        num = int(float(val))
        return f"{num:,} Kč".replace(",", " ")
    except (ValueError, TypeError):
        return "?"


def _generate_comment(row: pd.Series) -> str:
    """Generate a one-line Czech comment for why this property is interesting."""
    parts = []

    price = row.get("price_total")
    try:
        price_val = int(float(price))
        if price_val <= 8_000_000:
            parts.append("Velmi výhodná cena")
        elif price_val <= 12_000_000:
            parts.append("V rozpočtu")
        else:
            over = (price_val - 12_000_000) / 1_000_000
            parts.append(f"{over:.1f}M nad limitem")
    except (ValueError, TypeError):
        pass

    try:
        train_km = float(row.get("distance_to_train_km"))
        if train_km > 0:
            parts.append(f"vlak {train_km:.1f} km")
    except (ValueError, TypeError):
        pass

    if str(row.get("property_type", "")) == "house":
        parts.append("rodinný dům")

    transit = row.get("transit_dim")
    try:
        if float(transit) >= 100:
            parts.append("výborná doprava")
    except (ValueError, TypeError):
        pass

    size_cat = str(row.get("size_category", ""))
    if size_cat and size_cat.startswith(("4", "5")):
        parts.append(f"{size_cat}")

    return ". ".join(parts) if parts else "Dobré celkové skóre"
