"""HTTP/HTTPS endpoint checks, including TLS certificate expiry."""

from __future__ import annotations

import ssl
import time
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, HttpUrl, field_validator

from cloudsentinel.models import CheckStatus
from cloudsentinel.providers.base import (
    CheckContext,
    CheckOutcome,
    Provider,
    ProviderConfig,
    TargetNotAllowedError,
    apply_latency_threshold,
    ensure_target_allowed,
)

USER_AGENT = "CloudSentinel-HealthCheck/0.1 (+https://github.com/mohamedkhaled139/CloudSentinel)"


class HttpConfig(ProviderConfig):
    url: HttpUrl = Field(description="Endpoint to probe (http:// or https://)")
    method: Literal["GET", "HEAD", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict, description="Extra request headers")
    body: str | None = Field(default=None, description="Request body for POST", max_length=65536)
    expected_status_codes: list[int] = Field(
        default_factory=list, description="Accepted status codes. Empty means any 2xx/3xx."
    )
    body_contains: str | None = Field(default=None, description="Response body must contain this text")
    follow_redirects: bool = True
    verify_tls: bool = True
    tls_expiry_warning_days: int = Field(
        default=14, ge=0, le=365, description="Report degraded when the certificate expires sooner"
    )

    @field_validator("expected_status_codes")
    @classmethod
    def _codes(cls, v: list[int]) -> list[int]:
        for code in v:
            if not 100 <= code <= 599:
                raise ValueError(f"invalid HTTP status code {code}")
        return v


def _cert_not_after(response: httpx.Response) -> datetime | None:
    stream = response.extensions.get("network_stream")
    if stream is None:
        return None
    ssl_object = stream.get_extra_info("ssl_object")
    if ssl_object is None:
        return None
    cert = ssl_object.getpeercert()
    if not cert or "notAfter" not in cert:
        return None
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=UTC)


class HttpProvider(Provider):
    key = "http"
    label = "HTTP / HTTPS"
    description = (
        "Requests a URL and validates status code, body content, latency and TLS certificate expiry."
    )
    config_model = HttpConfig

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport  # injectable for tests

    async def check(self, ctx: CheckContext) -> CheckOutcome:
        cfg: HttpConfig = ctx.config
        url = str(cfg.url)
        host = urlsplit(url).hostname or ""
        try:
            await ensure_target_allowed(host, ctx.allow_private_targets)
        except TargetNotAllowedError as exc:
            return CheckOutcome.unknown(str(exc))

        headers = {"User-Agent": USER_AGENT, **cfg.headers}
        details: dict[str, Any] = {"url": url, "method": cfg.method}
        started = time.perf_counter()
        try:
            async with (
                httpx.AsyncClient(
                    timeout=ctx.timeout_seconds,
                    verify=cfg.verify_tls,
                    follow_redirects=cfg.follow_redirects,
                    transport=self._transport,
                ) as client,
                client.stream(cfg.method, url, headers=headers, content=cfg.body) as response,
            ):
                cert_expiry = None
                if url.startswith("https") and cfg.verify_tls:
                    try:
                        cert_expiry = _cert_not_after(response)
                    except Exception:
                        cert_expiry = None
                body = b""
                if cfg.body_contains is not None and cfg.method != "HEAD":
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > 1_000_000:
                            break
                latency_ms = (time.perf_counter() - started) * 1000
        except httpx.TimeoutException:
            return CheckOutcome(CheckStatus.down, f"Timed out after {ctx.timeout_seconds:g}s", None, details)
        except httpx.ConnectError as exc:
            return CheckOutcome(CheckStatus.down, f"Connection failed: {_short(exc)}", None, details)
        except httpx.HTTPError as exc:
            return CheckOutcome(CheckStatus.down, f"HTTP error: {_short(exc)}", None, details)

        details["status_code"] = response.status_code
        details["http_version"] = response.http_version
        if cert_expiry is not None:
            days_left = (cert_expiry - datetime.now(UTC)).total_seconds() / 86400
            details["tls_not_after"] = cert_expiry.isoformat()
            details["tls_days_remaining"] = round(days_left, 1)

        code_ok = (
            response.status_code in cfg.expected_status_codes
            if cfg.expected_status_codes
            else 200 <= response.status_code < 400
        )
        if not code_ok:
            return CheckOutcome(
                CheckStatus.down, f"Unexpected status {response.status_code}", round(latency_ms, 2), details
            )
        if cfg.body_contains is not None and cfg.body_contains.encode() not in body:
            return CheckOutcome(
                CheckStatus.down, "Response body did not contain expected text", round(latency_ms, 2), details
            )

        outcome = CheckOutcome(CheckStatus.up, f"HTTP {response.status_code}", round(latency_ms, 2), details)
        days = details.get("tls_days_remaining")
        if days is not None and days < cfg.tls_expiry_warning_days:
            outcome.status = CheckStatus.degraded
            outcome.message = f"TLS certificate expires in {days:.0f} days"
            if days <= 0:
                outcome.status = CheckStatus.down
                outcome.message = "TLS certificate has expired"
        return apply_latency_threshold(outcome, ctx.degraded_latency_ms)


def _short(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    return text[:300]
