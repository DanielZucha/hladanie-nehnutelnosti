"""Master CSV storage with schema enforcement, deduplication, and atomic writes."""

from dataclasses import dataclass, field, fields, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional
import hashlib
import tempfile
import shutil

import pandas as pd


@dataclass
class PropertyRecord:
    """Single property listing record."""

    property_id: str = ""
    source: str = ""  # sreality / ceskereality / idnes
    url: str = ""
    property_type: str = ""  # apartment / house
    size_category: str = ""  # 3+kk, 3+1, 4+kk, etc.
    size_sqm: Optional[float] = None
    garden_present: Optional[bool] = None
    greenery_score: Optional[float] = None  # 0-100, from greenery detection
    greenery_source: str = ""  # keyword / structured / osm / manual
    price_total: Optional[int] = None  # CZK
    price_per_sqm: Optional[float] = None
    location: str = ""
    district: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    distance_to_train_km: Optional[float] = None
    energy_efficiency_rating: str = ""  # A-G
    scrape_date: str = ""  # ISO date, first seen
    last_updated: str = ""  # ISO datetime
    composite_score: Optional[float] = None
    enrichment_status: str = "pending"  # pending / done / failed
    notes: str = ""  # manual notes column


SCHEMA_COLUMNS = [f.name for f in fields(PropertyRecord)]


def generate_property_id(source: str, url: str) -> str:
    """Deterministic ID from source + URL."""
    raw = f"{source}:{url}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def record_to_dict(rec: PropertyRecord) -> dict:
    """Convert record to dict for DataFrame row."""
    return asdict(rec)


def read_master(path: str | Path) -> pd.DataFrame:
    """Read master CSV, enforcing schema columns exist."""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=SCHEMA_COLUMNS)

    df = pd.read_csv(path, dtype=str, keep_default_na=False)

    for col in SCHEMA_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    return df[SCHEMA_COLUMNS]


def write_master(df: pd.DataFrame, path: str | Path) -> None:
    """Atomic write: write to temp file, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        suffix=".csv",
        delete=False,
    ) as tmp:
        df.to_csv(tmp, index=False)
        tmp_path = Path(tmp.name)

    shutil.move(str(tmp_path), str(path))


def deduplicate_and_merge(
    existing: pd.DataFrame,
    new_records: list[PropertyRecord],
) -> tuple[pd.DataFrame, int, int]:
    """Merge new records into existing DataFrame.

    Returns (merged_df, new_count, updated_count).
    """
    if not new_records:
        return existing, 0, 0

    new_rows = [record_to_dict(r) for r in new_records]
    new_df = pd.DataFrame(new_rows, columns=SCHEMA_COLUMNS)

    # Deduplicate within the new batch itself
    new_df = new_df.drop_duplicates(subset=["property_id"], keep="last")

    if existing.empty:
        return new_df, len(new_df), 0

    existing_ids = set(existing["property_id"].tolist())
    new_count = 0
    updated_count = 0
    rows_to_append = []

    for _, row in new_df.iterrows():
        pid = row["property_id"]
        if pid in existing_ids:
            # Check price change
            old_row = existing.loc[existing["property_id"] == pid].iloc[0]
            if str(row["price_total"]) != str(old_row["price_total"]):
                existing.loc[existing["property_id"] == pid, "price_total"] = str(
                    row["price_total"]
                )
                existing.loc[existing["property_id"] == pid, "last_updated"] = str(
                    row["last_updated"]
                )
                updated_count += 1
        else:
            rows_to_append.append(row)
            new_count += 1

    if rows_to_append:
        append_df = pd.DataFrame(rows_to_append, columns=SCHEMA_COLUMNS)
        existing = pd.concat([existing, append_df], ignore_index=True)

    return existing, new_count, updated_count


def record_price_change(
    history_path: str | Path,
    property_id: str,
    old_price: str,
    new_price: str,
) -> None:
    """Append a price change to the history CSV."""
    history_path = Path(history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "property_id": property_id,
        "old_price": old_price,
        "new_price": new_price,
        "change_date": datetime.now().isoformat(),
    }
    df = pd.DataFrame([row])

    if history_path.exists():
        df.to_csv(history_path, mode="a", header=False, index=False)
    else:
        df.to_csv(history_path, index=False)
