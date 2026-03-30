"""Report generation: HTML email with outlier table and scatter plot."""

import io
import logging
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def generate_scatter_plot(df: pd.DataFrame, outlier_ids: set[str]) -> bytes:
    """Generate 2D scatter plot: X=price efficiency, Y=greenery+transit.

    Returns PNG bytes.
    """
    plot_df = df.copy()
    plot_df["composite_score"] = pd.to_numeric(plot_df["composite_score"], errors="coerce")
    plot_df["price_score"] = pd.to_numeric(plot_df.get("price_score", 0), errors="coerce")
    plot_df["greenery_dim"] = pd.to_numeric(plot_df.get("greenery_dim", 0), errors="coerce")
    plot_df["transit_dim"] = pd.to_numeric(plot_df.get("transit_dim", 0), errors="coerce")
    plot_df = plot_df.dropna(subset=["price_score"])

    x = plot_df["price_score"].fillna(0)
    y = (plot_df["greenery_dim"].fillna(0) + plot_df["transit_dim"].fillna(0)) / 2

    is_outlier = plot_df["property_id"].isin(outlier_ids)

    fig, ax = plt.subplots(figsize=(10, 7))

    # Regular points
    ax.scatter(
        x[~is_outlier], y[~is_outlier],
        c="#94a3b8", alpha=0.5, s=30, label="Other listings",
    )
    # Outlier points
    ax.scatter(
        x[is_outlier], y[is_outlier],
        c="#ef4444", alpha=0.9, s=80, label="Top picks", zorder=5,
    )

    ax.set_xlabel("Price Efficiency (higher = more affordable)", fontsize=11)
    ax.set_ylabel("Greenery + Transit Score", fontsize=11)
    ax.set_title("Property Landscape", fontsize=13)
    ax.legend(loc="upper left")
    ax.set_xlim(-5, 105)
    ax.set_ylim(-5, 105)
    ax.grid(True, alpha=0.3)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generate_report_html(
    outliers: pd.DataFrame,
    total_count: int,
    new_count: int,
    scatter_cid: str = "scatter_plot",
) -> str:
    """Generate HTML report email body."""
    now = datetime.now().strftime("%d %B %Y")

    rows_html = ""
    for i, (_, row) in enumerate(outliers.iterrows(), 1):
        price_fmt = _format_price(row.get("price_total"))
        ppsqm_fmt = _format_price(row.get("price_per_sqm"))
        score = row.get("composite_score", "?")
        comment = _generate_comment(row)

        rows_html += f"""
        <tr style="border-bottom: 1px solid #e2e8f0;">
            <td style="padding: 8px;">{i}</td>
            <td style="padding: 8px;">
                <a href="{row.get('url', '#')}" style="color: #2563eb;">
                    {row.get('property_type', '?')} {row.get('size_category', '')}
                </a>
            </td>
            <td style="padding: 8px;">{row.get('location', '?')}</td>
            <td style="padding: 8px;">{price_fmt}</td>
            <td style="padding: 8px;">{ppsqm_fmt}/m2</td>
            <td style="padding: 8px;">{row.get('size_sqm', '?')} m2</td>
            <td style="padding: 8px; font-weight: bold;">{score}</td>
            <td style="padding: 8px; font-size: 0.9em; color: #475569;">{comment}</td>
        </tr>
        """

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px;">
        <h1 style="color: #1e293b;">Property Report -- {now}</h1>

        <div style="background: #f1f5f9; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px;">
            <strong>{total_count}</strong> total listings tracked |
            <strong>{new_count}</strong> new since last report
        </div>

        <h2 style="color: #334155;">Top Picks</h2>

        <table style="width: 100%; border-collapse: collapse; font-size: 0.95em;">
            <thead>
                <tr style="background: #f8fafc; border-bottom: 2px solid #cbd5e1;">
                    <th style="padding: 8px; text-align: left;">#</th>
                    <th style="padding: 8px; text-align: left;">Property</th>
                    <th style="padding: 8px; text-align: left;">Location</th>
                    <th style="padding: 8px; text-align: left;">Price</th>
                    <th style="padding: 8px; text-align: left;">CZK/m2</th>
                    <th style="padding: 8px; text-align: left;">Size</th>
                    <th style="padding: 8px; text-align: left;">Score</th>
                    <th style="padding: 8px; text-align: left;">Why</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>

        <h2 style="color: #334155; margin-top: 30px;">Market View</h2>
        <img src="cid:{scatter_cid}" style="width: 100%; max-width: 700px;" alt="Property scatter plot" />

        <hr style="margin-top: 30px; border: none; border-top: 1px solid #e2e8f0;" />
        <p style="font-size: 0.85em; color: #94a3b8;">
            Generated automatically. Scores reflect: price attractiveness (35%),
            greenery (25%), Prague transit (20%), room count (10%), property type (10%).
        </p>
    </body>
    </html>
    """
    return html


def _format_price(val) -> str:
    """Format price as '5,900,000 CZK'."""
    try:
        num = int(float(val))
        return f"{num:,} CZK".replace(",", " ")
    except (ValueError, TypeError):
        return "?"


def _generate_comment(row: pd.Series) -> str:
    """Generate a one-line analytical comment for why this property is interesting."""
    parts = []

    price = row.get("price_total")
    try:
        price_val = int(float(price))
        if price_val <= 8_000_000:
            parts.append("Very affordable")
        elif price_val <= 12_000_000:
            parts.append("Within budget")
        else:
            parts.append(f"{(price_val - 12_000_000) / 1_000_000:.1f}M over cap")
    except (ValueError, TypeError):
        pass

    greenery = row.get("greenery_dim") or row.get("greenery_score")
    try:
        if float(greenery) >= 80:
            parts.append("great greenery")
        elif float(greenery) >= 50:
            parts.append("park nearby")
    except (ValueError, TypeError):
        pass

    if str(row.get("property_type", "")) == "house":
        parts.append("house")

    transit = row.get("transit_dim")
    try:
        if float(transit) >= 100:
            parts.append("excellent transit")
    except (ValueError, TypeError):
        pass

    size_cat = str(row.get("size_category", ""))
    if size_cat and size_cat.startswith(("4", "5")):
        parts.append(f"{size_cat}")

    return ". ".join(parts) if parts else "Good composite score"
