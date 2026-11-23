"""Provider (check) and integration adapter contracts.

A **provider** knows how to execute one kind of health check (``http``, ``tcp``, ``prometheus`` ...).
An **integration adapter** knows how to connect to an external system (a Prometheus server, a
Kubernetes cluster) and is referenced by providers that need one.

Adding a new integration means implementing these two small interfaces and registering them in
``cloudsentinel.providers.registry``. See docs/integrations.md.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from cloudsentinel.models import CheckStatus


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class CheckOutcome:
    status: CheckStatus
    message: str = ""
    latency_ms: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def unknown(cls, message: str, **details: Any) -> CheckOutcome:
        return cls(CheckStatus.unknown, message, None, dict(details))


@dataclass
class IntegrationContext:
    id: int
    name: str
    kind: str
    config: dict[str, Any]
    secrets: dict[str, Any]


@dataclass
class CheckContext:
    monitor_id: int
    config: Any  # instance of the provider's config_model
    timeout_seconds: float
    degraded_latency_ms: float | None = None
    integration: IntegrationContext | None = None
    session_factory: sessionmaker[Session] | None = None
    allow_private_targets: bool = True


class Provider(ABC):
    key: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    config_model: ClassVar[type[ProviderConfig]]
    integration_kind: ClassVar[str | None] = None
    synthetic: ClassVar[bool] = False

    def validate_config(self, raw: dict[str, Any]) -> ProviderConfig:
        return self.config_model.model_validate(raw)

    @abstractmethod
    async def check(self, ctx: CheckContext) -> CheckOutcome: ...

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "integration_kind": self.integration_kind,
            "synthetic": self.synthetic,
            "config_schema": self.config_model.model_json_schema(),
        }


@dataclass
class ConnectionTestResult:
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class IntegrationAdapter(ABC):
    kind: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    config_model: ClassVar[type[ProviderConfig]]
    secret_model: ClassVar[type[ProviderConfig]]

    @abstractmethod
    async def test_connection(
        self, integration: IntegrationContext, timeout: float
    ) -> ConnectionTestResult: ...

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "description": self.description,
            "config_schema": self.config_model.model_json_schema(),
            "secret_schema": self.secret_model.model_json_schema(),
        }


def apply_latency_threshold(outcome: CheckOutcome, threshold_ms: float | None) -> CheckOutcome:
    if (
        threshold_ms is not None
        and outcome.status == CheckStatus.up
        and outcome.latency_ms is not None
        and outcome.latency_ms > threshold_ms
    ):
        outcome.status = CheckStatus.degraded
        outcome.message = (
            f"{outcome.message} (latency {outcome.latency_ms:.0f} ms > {threshold_ms:.0f} ms)".strip()
        )
    return outcome


class TargetNotAllowedError(Exception):
    pass


async def ensure_target_allowed(host: str, allow_private: bool) -> None:
    """Optionally refuse checks against loopback/private/link-local addresses (SSRF guard)."""
    if allow_private:
        return
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return  # resolution failure is reported by the check itself
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise TargetNotAllowedError(f"target {host} resolves to non-public address {addr}")
