"""API request/response schemas."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cloudsentinel.models import ComponentKind, Criticality, IncidentStatus, Role, Severity

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _email(v: str) -> str:
    v = v.strip().lower()
    if not _EMAIL.match(v) or len(v) > 254:
        raise ValueError("invalid email address")
    return v


# ---- auth / users ---------------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int
    user: UserOut


class UserOut(ORM):
    id: int
    email: str
    name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class UserCreate(BaseModel):
    email: str
    name: str = Field(default="", max_length=120)
    password: str = Field(min_length=10, max_length=256)
    role: Role = Role.viewer

    _v_email = field_validator("email")(_email)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=256)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


class SetupRequest(UserCreate):
    role: Role = Role.admin


# ---- components / topology ------------------------------------------------------------------------
class ComponentBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: ComponentKind = ComponentKind.service
    description: str = Field(default="", max_length=4000)
    owner: str = Field(default="", max_length=120)
    environment: str = Field(default="", max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=50)
    criticality: Criticality = Criticality.medium

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class ComponentCreate(ComponentBase):
    pass


class ComponentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: ComponentKind | None = None
    description: str | None = Field(default=None, max_length=4000)
    owner: str | None = Field(default=None, max_length=120)
    environment: str | None = Field(default=None, max_length=64)
    tags: list[str] | None = None
    criticality: Criticality | None = None


class ComponentHealthOut(BaseModel):
    own_status: str
    effective_status: str
    impacted_by: list[int]
    monitor_count: int


class ComponentOut(ORM):
    id: int
    name: str
    kind: str
    description: str
    owner: str
    environment: str
    tags: list[str]
    criticality: str
    is_synthetic: bool
    created_at: datetime
    health: ComponentHealthOut | None = None
    depends_on: list[int] = Field(default_factory=list)
    dependents: list[int] = Field(default_factory=list)
    open_incidents: int = 0
    availability_24h: float | None = None


class DependencyCreate(BaseModel):
    source_id: int = Field(description="The component that depends on target")
    target_id: int
    description: str = Field(default="", max_length=255)


class DependencyOut(ORM):
    id: int
    source_id: int
    target_id: int
    description: str


class TopologyOut(BaseModel):
    nodes: list[ComponentOut]
    edges: list[DependencyOut]


class ImpactOut(BaseModel):
    component_id: int
    upstream: list[int]
    downstream: list[int]


# ---- monitors -------------------------------------------------------------------------------------
class MonitorBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=32)
    component_id: int | None = None
    integration_id: int | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    interval_seconds: int = Field(default=60, ge=10, le=86400)
    timeout_seconds: float = Field(default=10, gt=0, le=120)
    failure_threshold: int = Field(default=3, ge=1, le=100)
    recovery_threshold: int = Field(default=2, ge=1, le=100)
    degraded_latency_ms: float | None = Field(default=None, gt=0)
    open_incident_on_degraded: bool = False


class MonitorCreate(MonitorBase):
    pass


class MonitorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    component_id: int | None = None
    integration_id: int | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=10, le=86400)
    timeout_seconds: float | None = Field(default=None, gt=0, le=120)
    failure_threshold: int | None = Field(default=None, ge=1, le=100)
    recovery_threshold: int | None = Field(default=None, ge=1, le=100)
    degraded_latency_ms: float | None = Field(default=None, gt=0)
    open_incident_on_degraded: bool | None = None


class MonitorOut(ORM):
    id: int
    name: str
    kind: str
    component_id: int | None
    integration_id: int | None
    config: dict[str, Any]
    enabled: bool
    interval_seconds: int
    timeout_seconds: float
    failure_threshold: int
    recovery_threshold: int
    degraded_latency_ms: float | None
    open_incident_on_degraded: bool
    last_status: str
    last_message: str
    last_checked_at: datetime | None
    consecutive_failures: int
    consecutive_successes: int
    is_synthetic: bool
    created_at: datetime


class CheckResultOut(ORM):
    id: int
    monitor_id: int
    checked_at: datetime
    status: str
    latency_ms: float | None
    message: str
    details: dict[str, Any]
    is_synthetic: bool


class MonitorStats(BaseModel):
    window_hours: int
    checks: int
    availability_pct: float | None
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    series: list[dict[str, Any]]


# ---- incidents ------------------------------------------------------------------------------------
class IncidentEventOut(ORM):
    id: int
    at: datetime
    kind: str
    message: str
    actor: str
    data: dict[str, Any]


class IncidentOut(ORM):
    id: int
    title: str
    status: IncidentStatus
    severity: Severity
    component_id: int | None
    monitor_id: int | None
    parent_id: int | None
    summary: str
    started_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_notes: str
    is_synthetic: bool
    component_name: str | None = None
    monitor_name: str | None = None
    child_count: int = 0


class IncidentDetail(IncidentOut):
    events: list[IncidentEventOut]
    children: list[IncidentOut]
    parent: IncidentOut | None
    evidence_results: list[CheckResultOut]
    affected_components: list[int]


class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    severity: Severity = Severity.major
    component_id: int | None = None
    summary: str = Field(default="", max_length=8000)


class IncidentAction(BaseModel):
    note: str = Field(default="", max_length=8000)


class IncidentNote(BaseModel):
    note: str = Field(min_length=1, max_length=8000)


# ---- risks ----------------------------------------------------------------------------------------
class RiskOut(ORM):
    id: int
    fingerprint: str
    category: str
    severity: str
    title: str
    description: str
    recommendation: str
    evidence: dict[str, Any]
    component_id: int | None
    monitor_id: int | None
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    dismissed_by: str | None
    is_synthetic: bool


# ---- integrations / agents ------------------------------------------------------------------------
class IntegrationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class IntegrationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict[str, Any] | None = None
    # Omitted secrets are kept; pass a key with null to remove it.
    secrets: dict[str, Any] | None = None
    enabled: bool | None = None


class IntegrationOut(ORM):
    id: int
    name: str
    kind: str
    config: dict[str, Any]
    secret_fields_set: list[str] = Field(default_factory=list)
    enabled: bool
    status: str
    last_checked_at: datetime | None
    last_error: str | None
    created_at: datetime
    monitor_count: int = 0


class ConnectionTestOut(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AgentOut(ORM):
    id: int
    name: str
    token_prefix: str
    hostname: str | None
    agent_version: str | None
    last_seen_at: datetime | None
    created_at: datetime


class AgentCreated(AgentOut):
    token: str = Field(description="Shown once. Store it in the agent's configuration.")


class FilesystemUsage(BaseModel):
    mount: str = Field(max_length=512)
    used_pct: float = Field(ge=0, le=100)
    total_bytes: int | None = Field(default=None, ge=0)


class AgentHeartbeat(BaseModel):
    hostname: str = Field(max_length=255)
    agent_version: str = Field(default="", max_length=32)
    boot_time: datetime | None = None
    load1: float | None = Field(default=None, ge=0)
    cpu_count: int | None = Field(default=None, ge=1, le=100000)
    memory_used_pct: float | None = Field(default=None, ge=0, le=100)
    filesystems: list[FilesystemUsage] = Field(default_factory=list, max_length=200)
    uptime_seconds: float | None = Field(default=None, ge=0)


# ---- overview / settings / misc -------------------------------------------------------------------
class OverviewOut(BaseModel):
    generated_at: datetime
    demo_mode: bool
    contains_synthetic_data: bool
    components: dict[str, int]
    monitors: dict[str, int]
    availability_24h: float | None
    availability_7d: float | None
    open_incidents: list[IncidentOut]
    open_risks: dict[str, int]
    top_risks: list[RiskOut]
    recent_results: list[dict[str, Any]]
    availability_trend: list[dict[str, Any]]


class AppSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retention_check_results_days: int | None = Field(default=None, ge=1, le=3650)
    retention_agent_reports_days: int | None = Field(default=None, ge=1, le=3650)
    retention_resolved_incidents_days: int | None = Field(default=None, ge=7, le=3650)
    retention_resolved_risks_days: int | None = Field(default=None, ge=1, le=3650)
    auto_resolve_incidents: bool | None = None
    risk_lookback_days: int | None = Field(default=None, ge=1, le=90)
    recurring_failure_min_episodes: int | None = Field(default=None, ge=2, le=100)
    latency_regression_ratio: float | None = Field(default=None, ge=1.1, le=20)
    latency_min_samples: int | None = Field(default=None, ge=5, le=10000)
    restart_min_count: int | None = Field(default=None, ge=2, le=1000)
    stale_interval_multiplier: float | None = Field(default=None, ge=1.5, le=100)
    availability_target_pct: float | None = Field(default=None, ge=50, le=100)
    tls_expiry_risk_days: int | None = Field(default=None, ge=1, le=365)


class SystemInfo(BaseModel):
    version: str
    environment: str
    database: str
    demo_mode: bool
    scheduler_enabled: bool
    scheduler_healthy: bool | None
    allow_private_targets: bool


class Page(BaseModel):
    total: int
    items: list[Any]


ReportSection = Literal["monitors", "components", "incidents"]

TokenResponse.model_rebuild()
