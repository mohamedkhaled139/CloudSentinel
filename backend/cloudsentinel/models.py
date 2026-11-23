"""ORM models.

Conventions:
* All timestamps are timezone-aware UTC.
* Enumerations are stored as short strings for portability between SQLite and PostgreSQL.
* ``is_synthetic`` marks demo data so it can never be confused with real monitoring results.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cloudsentinel.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores datetimes as UTC and always returns aware datetimes (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Role(StrEnum):
    admin = "admin"  # full access incl. users, settings, integrations
    operator = "operator"  # manage monitors, components, incidents, risks
    viewer = "viewer"  # read-only


class CheckStatus(StrEnum):
    up = "up"
    degraded = "degraded"
    down = "down"
    unknown = "unknown"  # the check could not produce a verdict (misconfiguration, provider error)


class ComponentKind(StrEnum):
    service = "service"
    application = "application"
    server = "server"
    database = "database"
    network = "network"
    storage = "storage"
    kubernetes = "kubernetes"
    cloud = "cloud"
    other = "other"


class Criticality(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class IncidentStatus(StrEnum):
    detected = "detected"
    investigating = "investigating"
    resolved = "resolved"


class Severity(StrEnum):
    info = "info"
    minor = "minor"
    major = "major"
    critical = "critical"


class RiskStatus(StrEnum):
    open = "open"
    dismissed = "dismissed"
    resolved = "resolved"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=Role.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Integration(Base):
    """A configured external data source (Prometheus server, Kubernetes cluster, ...)."""

    __tablename__ = "integrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Fernet-encrypted JSON blob of secret fields (tokens, passwords). Never returned by the API.
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok | error | unknown
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Component(Base):
    """An infrastructure component or service that can be monitored and depended upon."""

    __tablename__ = "components"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(32), default=ComponentKind.service)
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(120), default="")
    environment: Mapped[str] = mapped_column(String(64), default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    criticality: Mapped[str] = mapped_column(String(16), default=Criticality.medium)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    monitors: Mapped[list[Monitor]] = relationship(back_populates="component")


class Dependency(Base):
    """``source`` depends on ``target``: if target fails, source is impacted."""

    __tablename__ = "dependencies"
    __table_args__ = (UniqueConstraint("source_id", "target_id", name="uq_dependency_edge"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("components.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("components.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Agent(Base):
    """A lightweight Linux host agent that pushes host metrics."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    token_prefix: Mapped[str] = mapped_column(String(16))
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AgentReport(Base):
    __tablename__ = "agent_reports"
    __table_args__ = (Index("ix_agent_reports_agent_time", "agent_id", "received_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    boot_time: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    load1: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_used_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_used_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # fullest mount
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Monitor(Base):
    __tablename__ = "monitors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), index=True)  # provider key: http, tcp, prometheus, ...
    component_id: Mapped[int | None] = mapped_column(
        ForeignKey("components.id", ondelete="SET NULL"), nullable=True, index=True
    )
    integration_id: Mapped[int | None] = mapped_column(
        ForeignKey("integrations.id", ondelete="SET NULL"), nullable=True
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=10.0)
    # Consecutive non-up results before an incident opens / consecutive up results before it recovers.
    failure_threshold: Mapped[int] = mapped_column(Integer, default=3)
    recovery_threshold: Mapped[int] = mapped_column(Integer, default=2)
    degraded_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_incident_on_degraded: Mapped[bool] = mapped_column(Boolean, default=False)

    last_status: Mapped[str] = mapped_column(String(16), default=CheckStatus.unknown)
    last_message: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_successes: Mapped[int] = mapped_column(Integer, default=0)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    component: Mapped[Component | None] = relationship(back_populates="monitors")
    integration: Mapped[Integration | None] = relationship()


class CheckResult(Base):
    __tablename__ = "check_results"
    __table_args__ = (Index("ix_check_results_monitor_time", "monitor_id", "checked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"))
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default=IncidentStatus.detected, index=True)
    severity: Mapped[str] = mapped_column(String(16), default=Severity.major)
    component_id: Mapped[int | None] = mapped_column(
        ForeignKey("components.id", ondelete="SET NULL"), nullable=True, index=True
    )
    monitor_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Correlation: an incident likely caused by an upstream dependency's incident.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    resolution_notes: Mapped[str] = mapped_column(Text, default="")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)

    component: Mapped[Component | None] = relationship()
    monitor: Mapped[Monitor | None] = relationship()
    events: Mapped[list[IncidentEvent]] = relationship(
        back_populates="incident", order_by="IncidentEvent.at", cascade="all, delete-orphan"
    )


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(254), default="system")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    incident: Mapped[Incident] = relationship(back_populates="events")


class RiskFinding(Base):
    __tablename__ = "risk_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable identity of a finding (category + subject); used to update instead of duplicate.
    fingerprint: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    component_id: Mapped[int | None] = mapped_column(
        ForeignKey("components.id", ondelete="CASCADE"), nullable=True
    )
    monitor_id: Mapped[int | None] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default=RiskStatus.open, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    dismissed_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(255), default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
