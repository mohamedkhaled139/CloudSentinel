"""TCP connectivity checks."""

from __future__ import annotations

import asyncio
import contextlib
import time

from pydantic import Field

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


class TcpConfig(ProviderConfig):
    host: str = Field(min_length=1, max_length=253, description="Hostname or IP address")
    port: int = Field(ge=1, le=65535)
    expect_banner: str | None = Field(
        default=None, max_length=256, description="Optional text the server must send after connecting"
    )


class TcpProvider(Provider):
    key = "tcp"
    label = "TCP port"
    description = "Opens a TCP connection and optionally validates the server banner."
    config_model = TcpConfig

    async def check(self, ctx: CheckContext) -> CheckOutcome:
        cfg: TcpConfig = ctx.config
        details = {"host": cfg.host, "port": cfg.port}
        try:
            await ensure_target_allowed(cfg.host, ctx.allow_private_targets)
        except TargetNotAllowedError as exc:
            return CheckOutcome.unknown(str(exc))
        started = time.perf_counter()
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(cfg.host, cfg.port), timeout=ctx.timeout_seconds
            )
            latency_ms = (time.perf_counter() - started) * 1000
            if cfg.expect_banner:
                remaining = max(0.1, ctx.timeout_seconds - latency_ms / 1000)
                data = await asyncio.wait_for(reader.read(1024), timeout=remaining)
                banner = data.decode(errors="replace")
                details["banner"] = banner[:200]
                if cfg.expect_banner not in banner:
                    return CheckOutcome(
                        CheckStatus.down, "Banner did not match expectation", round(latency_ms, 2), details
                    )
        except TimeoutError:
            return CheckOutcome(CheckStatus.down, f"Timed out after {ctx.timeout_seconds:g}s", None, details)
        except OSError as exc:
            return CheckOutcome(CheckStatus.down, f"Connection failed: {exc.strerror or exc}", None, details)
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        outcome = CheckOutcome(
            CheckStatus.up, f"Connected to {cfg.host}:{cfg.port}", round(latency_ms, 2), details
        )
        return apply_latency_threshold(outcome, ctx.degraded_latency_ms)
