"""Linux host health from the CloudSentinel agent (push-based heartbeat reports)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from pydantic import Field
from sqlalchemy import select

from cloudsentinel.models import Agent, AgentReport, CheckStatus
from cloudsentinel.providers.base import CheckContext, CheckOutcome, Provider, ProviderConfig


class AgentCheckConfig(ProviderConfig):
    agent_id: int = Field(ge=1, description="Registered agent to evaluate")
    max_staleness_seconds: int = Field(
        default=180, ge=30, le=86400, description="Report age after which host is DOWN"
    )
    load_per_core_degraded: float | None = Field(default=2.0, ge=0, description="1-min load / CPU count")
    memory_degraded_pct: float | None = Field(default=90, ge=0, le=100)
    disk_degraded_pct: float | None = Field(default=85, ge=0, le=100)
    disk_down_pct: float | None = Field(default=97, ge=0, le=100)


class AgentProvider(Provider):
    key = "agent"
    label = "Linux host (agent)"
    description = (
        "Evaluates heartbeat freshness, load, memory and disk usage reported by the CloudSentinel agent."
    )
    config_model = AgentCheckConfig

    async def check(self, ctx: CheckContext) -> CheckOutcome:
        cfg: AgentCheckConfig = ctx.config
        if ctx.session_factory is None:
            return CheckOutcome.unknown("database unavailable")
        found = await asyncio.to_thread(self._load, ctx, cfg.agent_id)
        if found is None:
            return CheckOutcome.unknown(f"Agent {cfg.agent_id} is not registered")
        agent_name, report = found
        if report is None:
            return CheckOutcome(CheckStatus.down, f"Agent '{agent_name}' has never reported", None, {})
        age = (datetime.now(UTC) - report.received_at).total_seconds()
        details: dict[str, Any] = {
            "agent": agent_name,
            "report_age_seconds": round(age, 1),
            "boot_time": report.boot_time.isoformat() if report.boot_time else None,
            "load1": report.load1,
            "cpu_count": report.cpu_count,
            "memory_used_pct": report.memory_used_pct,
            "disk_used_pct": report.disk_used_pct,
            "filesystems": report.data.get("filesystems", []),
        }
        if age > cfg.max_staleness_seconds:
            return CheckOutcome(
                CheckStatus.down,
                f"No heartbeat for {age:.0f}s (limit {cfg.max_staleness_seconds}s)",
                None,
                details,
            )

        problems_down: list[str] = []
        problems_degraded: list[str] = []
        if cfg.disk_down_pct is not None and (report.disk_used_pct or 0) >= cfg.disk_down_pct:
            problems_down.append(f"disk {report.disk_used_pct:.0f}% used")
        elif cfg.disk_degraded_pct is not None and (report.disk_used_pct or 0) >= cfg.disk_degraded_pct:
            problems_degraded.append(f"disk {report.disk_used_pct:.0f}% used")
        if cfg.memory_degraded_pct is not None and (report.memory_used_pct or 0) >= cfg.memory_degraded_pct:
            problems_degraded.append(f"memory {report.memory_used_pct:.0f}% used")
        if cfg.load_per_core_degraded is not None and report.load1 is not None and report.cpu_count:
            per_core = report.load1 / report.cpu_count
            details["load_per_core"] = round(per_core, 2)
            if per_core >= cfg.load_per_core_degraded:
                problems_degraded.append(f"load {per_core:.2f}/core")

        if problems_down:
            return CheckOutcome(CheckStatus.down, "; ".join(problems_down + problems_degraded), None, details)
        if problems_degraded:
            return CheckOutcome(CheckStatus.degraded, "; ".join(problems_degraded), None, details)
        return CheckOutcome(CheckStatus.up, f"Host healthy (heartbeat {age:.0f}s ago)", None, details)

    @staticmethod
    def _load(ctx: CheckContext, agent_id: int) -> tuple[str, AgentReport | None] | None:
        assert ctx.session_factory is not None
        with ctx.session_factory() as db:
            agent = db.get(Agent, agent_id)
            if agent is None:
                return None
            report = db.scalars(
                select(AgentReport)
                .where(AgentReport.agent_id == agent_id)
                .order_by(AgentReport.received_at.desc())
                .limit(1)
            ).first()
            if report is not None:
                db.expunge(report)
            return agent.name, report
