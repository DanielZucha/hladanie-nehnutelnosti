"""Configuration loader for home-searching pipeline."""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator


class EmailConfig(BaseModel):
    address: str = ""
    app_password: str = ""
    alert_senders: list[str] = []
    report_recipients: list[str] = []
    report_days: list[int] = [0, 3]
    report_hour_cet: int = 9


class CorridorBox(BaseModel):
    lat_min: float = 50.03
    lat_max: float = 50.17
    lon_min: float = 14.55
    lon_max: float = 14.77


class SearchConfig(BaseModel):
    sreality_region_id: int = 10  # Praha
    sreality_district_ids: str = "56"  # Praha-východ
    apartment_sub_cbs: str = "6,7,8,9,10,11,12"  # 3+kk .. 6+
    house_sub_cbs: str = "37,39,54"  # rodinný, vila, vícegenerační
    building_types: str = "1,2,3,4,6,7,8"  # all except 5 = panelová
    price_min_czk: int = 6_000_000
    price_max_czk: int = 14_000_000
    min_area_sqm: float = 79
    min_rooms: int = 3
    corridor: CorridorBox = CorridorBox()
    train_max_km: float = 4.0
    exclude_towns: list[str] = ["Škvorec"]  # station near in a straight line, but bus-only in practice


class TransitConfig(BaseModel):
    prague_score: int = 100
    train_within_3km: int = 100
    train_within_6km: int = 50
    no_train: int = 0


class ScoringConfig(BaseModel):
    weights: dict[str, float] = {
        "price_attractiveness": 0.45,
        "transit": 0.25,
        "room_count": 0.15,
        "property_type": 0.15,
    }
    price_cap_czk: int = 12_000_000
    price_penalty_per_million_over: int = 10
    house_premium_tolerance: float = 0.25
    room_scores: dict[str, int] = {}
    type_scores: dict[str, int] = {}
    transit: TransitConfig = TransitConfig()

    @field_validator("weights")
    @classmethod
    def weights_sum_to_one(cls, v: dict[str, float]) -> dict[str, float]:
        total = sum(v.values())
        if abs(total - 1.0) > 0.01:
            msg = f"Scoring weights must sum to 1.0, got {total}"
            raise ValueError(msg)
        return v


class EnricherConfig(BaseModel):
    request_delay_seconds: list[float] = [2.0, 4.0]
    max_retries: int = 1
    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


class DriveConfig(BaseModel):
    remote_folder: str = "Home Search - Prague"


class DataConfig(BaseModel):
    master_csv: str = "data/master_v2.csv"
    price_history_csv: str = "data/price_history.csv"
    failed_parses_dir: str = "data/failed_parses"


class AppConfig(BaseModel):
    email: EmailConfig = EmailConfig()
    search: SearchConfig = SearchConfig()
    scoring: ScoringConfig = ScoringConfig()
    enricher: EnricherConfig = EnricherConfig()
    drive: DriveConfig = DriveConfig()
    data: DataConfig = DataConfig()


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load configuration from YAML file, with env var overrides."""
    config_path = Path(path)
    if not config_path.exists():
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)

    with open(config_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    config = AppConfig(**raw)

    # Environment variable overrides (secrets stay out of config.yaml)
    if addr := os.environ.get("EMAIL_ADDRESS"):
        config.email.address = addr
    if pw := os.environ.get("EMAIL_APP_PASSWORD"):
        config.email.app_password = pw
    if recip := os.environ.get("REPORT_RECIPIENTS"):
        config.email.report_recipients = [r.strip() for r in recip.split(",")]
    return config
