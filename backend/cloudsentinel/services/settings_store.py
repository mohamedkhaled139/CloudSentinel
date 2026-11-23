"""Runtime-editable application settings persisted in the database."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from cloudsentinel.models import AppSetting

KEY = "app"


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Retention
    retention_check_results_days: int = Field(default=30, ge=1, le=3650)
    retention_agent_reports_days: int = Field(default=7, ge=1, le=3650)
    retention_resolved_incidents_days: int = Field(default=365, ge=7, le=3650)
    retention_resolved_risks_days: int = Field(default=90, ge=1, le=3650)
    # Incidents
    auto_resolve_incidents: bool = True
    # Risk analysis
    risk_lookback_days: int = Field(default=7, ge=1, le=90)
    recurring_failure_min_episodes: int = Field(default=3, ge=2, le=100)
    latency_regression_ratio: float = Field(default=1.5, ge=1.1, le=20)
    latency_min_samples: int = Field(default=20, ge=5, le=10000)
    restart_min_count: int = Field(default=3, ge=2, le=1000)
    stale_interval_multiplier: float = Field(default=3.0, ge=1.5, le=100)
    availability_target_pct: float = Field(default=99.9, ge=50, le=100)
    tls_expiry_risk_days: int = Field(default=21, ge=1, le=365)


def get_app_settings(db: Session) -> AppSettings:
    row = db.get(AppSetting, KEY)
    return AppSettings.model_validate(row.value if row else {})


def update_app_settings(db: Session, changes: dict[str, Any]) -> AppSettings:
    current = get_app_settings(db)
    merged = AppSettings.model_validate({**current.model_dump(), **changes})
    row = db.get(AppSetting, KEY)
    if row is None:
        db.add(AppSetting(key=KEY, value=merged.model_dump()))
    else:
        row.value = merged.model_dump()
    db.flush()
    return merged
