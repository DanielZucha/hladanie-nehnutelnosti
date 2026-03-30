"""One-time market analysis on bulk-scraped dataset.

Defines peer-group baselines and identifies analytically interesting
listings relative to their group. Not used in the daily email pipeline.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PEER_GROUPS = {
    "apt_prague": "Byty -- Praha",
    "apt_region": "Byty -- okolí",
    "house_prague": "Domy -- Praha",
    "house_region": "Domy -- okolí",
}


def assign_peer_group(row: pd.Series) -> str:
    ptype = "house" if row.get("property_type") == "house" else "apt"
    combined = f"{row.get('district', '')} {row.get('location', '')}".lower()
    loc = "prague" if "praha" in combined else "region"
    return f"{ptype}_{loc}"


def compute_peer_baselines(df: pd.DataFrame) -> pd.DataFrame:
    """Compute median, mean, std for key metrics per peer group."""
    df = df.copy()
    df["peer_group"] = df.apply(assign_peer_group, axis=1)

    priced = df[df["price_total"] > 1]

    rows = []
    for pg, label in PEER_GROUPS.items():
        sub = priced[priced["peer_group"] == pg]
        all_sub = df[df["peer_group"] == pg]
        if sub.empty:
            continue
        rows.append({
            "peer_group": pg,
            "label": label,
            "count": len(all_sub),
            "count_priced": len(sub),
            "price_total_median": sub["price_total"].median(),
            "price_total_q1": sub["price_total"].quantile(0.25),
            "price_total_q3": sub["price_total"].quantile(0.75),
            "price_sqm_median": sub["price_per_sqm"].median(),
            "price_sqm_q1": sub["price_per_sqm"].quantile(0.25),
            "price_sqm_q3": sub["price_per_sqm"].quantile(0.75),
            "price_sqm_std": sub["price_per_sqm"].std(),
            "size_sqm_median": all_sub["size_sqm"].median(),
            "greenery_pct": (all_sub["greenery_score"] > 0).mean() * 100,
            "garden_pct": (all_sub["garden_present"] == True).mean() * 100,
        })

    return pd.DataFrame(rows)


def score_relative_to_peers(df: pd.DataFrame) -> pd.DataFrame:
    """Add z-score columns relative to peer group.

    New columns:
    - peer_group: group assignment
    - price_z: how cheap relative to group (higher = cheaper)
    - greenery_z: how green relative to group
    - composite_z: how good overall relative to group
    """
    df = df.copy()
    for col in ["price_per_sqm", "greenery_score", "composite_score"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["peer_group"] = df.apply(assign_peer_group, axis=1)

    def _neg_zscore(s):
        """Inverted z-score: lower value = higher score."""
        std = s.std()
        if std == 0 or pd.isna(std):
            return pd.Series(0.0, index=s.index)
        return -(s - s.mean()) / std

    def _zscore(s):
        std = s.std()
        if std == 0 or pd.isna(std):
            return pd.Series(0.0, index=s.index)
        return (s - s.mean()) / std

    df["price_z"] = df.groupby("peer_group")["price_per_sqm"].transform(
        lambda s: _neg_zscore(s.fillna(s.median()))
    )
    df["greenery_z"] = df.groupby("peer_group")["greenery_score"].transform(
        lambda s: _zscore(s.fillna(0))
    )
    df["composite_z"] = df.groupby("peer_group")["composite_score"].transform(
        lambda s: _zscore(s.fillna(s.median()))
    )

    return df


def pick_interesting(df: pd.DataFrame, per_group: int = 5) -> dict[str, pd.DataFrame]:
    """Identify interesting listings per peer group.

    Returns dict with keys:
    - "winners": top composite_z per group (best overall for their segment)
    - "bargains": top price_z per group (cheapest relative to segment)
    - "green_gems": top greenery_z per group (greenest relative to segment)
    - "baselines": peer group summary statistics
    """
    scored = score_relative_to_peers(df)

    # Exclude price-on-request from bargains
    priced = scored[scored["price_total"] > 1]

    seen_ids = set()
    result = {}

    # 1. Winners: highest composite z-score per group
    winners = (
        scored
        .sort_values("composite_z", ascending=False)
        .groupby("peer_group")
        .head(per_group)
        .sort_values(["peer_group", "composite_z"], ascending=[True, False])
    )
    seen_ids.update(winners["property_id"].tolist())
    result["winners"] = winners

    # 2. Bargains: highest price z-score per group (not already a winner)
    bargain_pool = priced[~priced["property_id"].isin(seen_ids)]
    bargains = (
        bargain_pool
        .sort_values("price_z", ascending=False)
        .groupby("peer_group")
        .head(per_group)
        .sort_values(["peer_group", "price_z"], ascending=[True, False])
    )
    seen_ids.update(bargains["property_id"].tolist())
    result["bargains"] = bargains

    # 3. Green gems: highest greenery z-score per group (not already picked)
    green_pool = scored[~scored["property_id"].isin(seen_ids)]
    green_pool = green_pool[green_pool["greenery_score"] > 0]
    greens = (
        green_pool
        .sort_values("greenery_z", ascending=False)
        .groupby("peer_group")
        .head(per_group)
        .sort_values(["peer_group", "greenery_z"], ascending=[True, False])
    )
    result["green_gems"] = greens

    result["baselines"] = compute_peer_baselines(df)

    return result


def format_analysis_report(picks: dict[str, pd.DataFrame]) -> str:
    """Format the analysis as a readable text report."""
    lines = []
    lines.append("=" * 70)
    lines.append("MARKET ANALYSIS -- PEER GROUP BASELINES & PICKS")
    lines.append("=" * 70)

    baselines = picks["baselines"]
    lines.append("")
    lines.append("BASELINES (median values per peer group)")
    lines.append("-" * 70)
    for _, row in baselines.iterrows():
        lines.append(f"\n  {row['label']} (n={row['count']}, {row['count_priced']} with price)")
        lines.append(f"    Price:      {row['price_total_median']:>12,.0f} CZK  [Q1={row['price_total_q1']:,.0f}  Q3={row['price_total_q3']:,.0f}]")
        lines.append(f"    Price/sqm:  {row['price_sqm_median']:>12,.0f} CZK  [Q1={row['price_sqm_q1']:,.0f}  Q3={row['price_sqm_q3']:,.0f}]  std={row['price_sqm_std']:,.0f}")
        lines.append(f"    Size:       {row['size_sqm_median']:>12,.0f} sqm")
        lines.append(f"    Garden:     {row['garden_pct']:>11.0f}%")

    section_config = {
        "winners": ("WINNERS (best composite score for their segment)", "composite_z"),
        "bargains": ("BARGAINS (cheapest per sqm for their segment)", "price_z"),
        "green_gems": ("GREEN GEMS (most greenery for their segment)", "greenery_z"),
    }

    for key, (title, z_col) in section_config.items():
        section_df = picks[key]
        if section_df.empty:
            continue

        lines.append("")
        lines.append(title)
        lines.append("-" * 70)

        for pg, label in PEER_GROUPS.items():
            group_picks = section_df[section_df["peer_group"] == pg]
            if group_picks.empty:
                continue
            lines.append(f"\n  {label}:")
            for _, row in group_picks.iterrows():
                price = row["price_total"]
                price_str = f"{int(price):,} CZK".replace(",", " ") if price > 1 else "na dotaz"
                ppsqm = row.get("price_per_sqm")
                ppsqm_str = f"{int(ppsqm):,}/sqm".replace(",", " ") if pd.notna(ppsqm) and ppsqm > 0 else ""
                lines.append(
                    f"    z={row[z_col]:+.1f}  {price_str:>18s}  {ppsqm_str:>12s}  "
                    f"{row.get('size_sqm', '?'):>4.0f}m2  "
                    f"{row.get('location', '?')[:40]}"
                )
                lines.append(f"           {row.get('url', '')}")

    return "\n".join(lines)
