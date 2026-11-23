"""Synthetic provider for the demo environment.

Produces *simulated* results so the UI can be explored without real infrastructure. It is only
available when ``CS_DEMO_MODE=true`` and every result it produces is stored with
``is_synthetic=True`` and labelled as synthetic in the UI and API.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from pydantic import Field

from cloudsentinel.models import CheckStatus
from cloudsentinel.providers.base import CheckContext, CheckOutcome, Provider, ProviderConfig


class SyntheticConfig(ProviderConfig):
    base_latency_ms: float = Field(default=80, ge=0, le=60000)
    jitter_ms: float = Field(default=20, ge=0, le=60000)
    failure_rate: float = Field(default=0.0, ge=0, le=1, description="Random probability of a DOWN result")
    degraded_rate: float = Field(
        default=0.0, ge=0, le=1, description="Random probability of a DEGRADED result"
    )
    outage_minute_start: int | None = Field(
        default=None,
        ge=0,
        le=59,
        description="Simulate a recurring outage starting at this minute of each hour",
    )
    outage_minutes: int = Field(default=0, ge=0, le=59, description="Length of the recurring outage")
    outage_every_hours: int = Field(
        default=1, ge=1, le=24 * 30, description="Repeat the outage every N hours"
    )


def simulate(cfg: SyntheticConfig, at: datetime, rng: random.Random) -> CheckOutcome:
    details = {"synthetic": True}
    if cfg.outage_minute_start is not None and cfg.outage_minutes > 0:
        hour_index = int(at.timestamp() // 3600)
        offset = at.minute - cfg.outage_minute_start
        if hour_index % cfg.outage_every_hours == 0 and 0 <= offset < cfg.outage_minutes:
            return CheckOutcome(CheckStatus.down, "Simulated outage window (synthetic)", None, details)
    roll = rng.random()
    latency = max(1.0, rng.gauss(cfg.base_latency_ms, cfg.jitter_ms or 0.0001))
    if roll < cfg.failure_rate:
        return CheckOutcome(CheckStatus.down, "Simulated failure (synthetic)", None, details)
    if roll < cfg.failure_rate + cfg.degraded_rate:
        return CheckOutcome(
            CheckStatus.degraded, "Simulated slow response (synthetic)", round(latency * 4, 2), details
        )
    return CheckOutcome(CheckStatus.up, "Simulated OK (synthetic)", round(latency, 2), details)


class SyntheticProvider(Provider):
    key = "synthetic"
    label = "Synthetic (demo only)"
    description = "Generates simulated results for the demo environment. Never use for real monitoring."
    config_model = SyntheticConfig
    synthetic = True

    def __init__(self) -> None:
        self._rng = random.Random()

    async def check(self, ctx: CheckContext) -> CheckOutcome:
        return simulate(ctx.config, datetime.now(UTC), self._rng)
