#!/usr/bin/env python3
"""CloudSentinel Linux host agent.

A single-file, dependency-free agent (Python 3.8+) that reports host health to a CloudSentinel
server every ``--interval`` seconds: load average, CPU count, memory usage, filesystem usage and
boot time (used to detect unplanned reboots).

Configuration (environment variables or flags):
  CLOUDSENTINEL_URL     Base URL of the CloudSentinel server, e.g. https://sentinel.example.com
  CLOUDSENTINEL_TOKEN   Agent token from Integrations -> Linux host agents -> Register

Usage:
  cloudsentinel_agent.py                 run in the foreground
  cloudsentinel_agent.py --once          send a single report and exit (useful for testing)
  cloudsentinel_agent.py --print         print the report without sending it
  sudo cloudsentinel_agent.py --install  install as a systemd service (runs as a dedicated user)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

VERSION = "0.1.0"
IGNORED_FS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "overlay", "squashfs", "securityfs",
    "pstore", "debugfs", "tracefs", "mqueue", "hugetlbfs", "configfs", "fusectl", "binfmt_misc", "autofs",
    "nsfs", "bpf", "ramfs", "rpc_pipefs", "efivarfs",
}


def read_boot_time() -> datetime | None:
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime "):
                    return datetime.fromtimestamp(int(line.split()[1]), tz=timezone.utc)
    except OSError:
        return None
    return None


def read_uptime() -> float | None:
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError):
        return None


def read_memory_used_pct() -> float | None:
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key] = int(rest.strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    total = info.get("MemTotal")
    available = info.get("MemAvailable")
    if not total or available is None:
        return None
    return round(100.0 * (total - available) / total, 2)


def read_filesystems() -> list[dict[str, object]]:
    result = []
    seen: set[str] = set()
    try:
        with open("/proc/mounts") as f:
            mounts = [line.split() for line in f]
    except OSError:
        return result
    for parts in mounts:
        if len(parts) < 3:
            continue
        device, mount, fstype = parts[0], parts[1], parts[2]
        if fstype in IGNORED_FS or mount in seen or device in seen:
            continue
        if mount.startswith(("/proc", "/sys", "/dev", "/run", "/snap")):
            continue
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        if usage.total == 0:
            continue
        seen.add(mount)
        seen.add(device)
        result.append({
            "mount": mount[:512],
            "used_pct": round(100.0 * usage.used / usage.total, 2),
            "total_bytes": usage.total,
        })
    return result[:200]


def collect() -> dict[str, object]:
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = None
    boot = read_boot_time()
    return {
        "hostname": socket.getfqdn()[:255] or platform.node(),
        "agent_version": VERSION,
        "boot_time": boot.isoformat() if boot else None,
        "load1": round(load1, 3) if load1 is not None else None,
        "cpu_count": os.cpu_count(),
        "memory_used_pct": read_memory_used_pct(),
        "filesystems": read_filesystems(),
        "uptime_seconds": read_uptime(),
    }


def send(url: str, token: str, payload: dict[str, object], timeout: float, insecure: bool) -> int:
    req = urllib.request.Request(
        url.rstrip("/") + "/api/v1/agent/heartbeat",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 "User-Agent": f"cloudsentinel-agent/{VERSION}"},
    )
    context = ssl._create_unverified_context() if insecure else None  # noqa: S323 - explicit opt-in
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:  # noqa: S310 - URL from config
        return int(resp.status)


SERVICE = """[Unit]
Description=CloudSentinel host agent
After=network-online.target
Wants=network-online.target

[Service]
User=cloudsentinel-agent
EnvironmentFile=/etc/cloudsentinel-agent.env
ExecStart=/usr/bin/env python3 /usr/local/bin/cloudsentinel_agent.py
Restart=always
RestartSec=10
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""


def install(url: str, token: str) -> int:
    if os.geteuid() != 0:
        print("--install must run as root", file=sys.stderr)
        return 1
    if not url or not token:
        print("CLOUDSENTINEL_URL and CLOUDSENTINEL_TOKEN are required", file=sys.stderr)
        return 1
    shutil.copy(os.path.abspath(__file__), "/usr/local/bin/cloudsentinel_agent.py")
    os.chmod("/usr/local/bin/cloudsentinel_agent.py", 0o755)
    fd = os.open("/etc/cloudsentinel-agent.env", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"CLOUDSENTINEL_URL={url}\nCLOUDSENTINEL_TOKEN={token}\n")
    if os.system("id -u cloudsentinel-agent >/dev/null 2>&1") != 0:  # noqa: S605
        os.system("useradd --system --no-create-home --shell /usr/sbin/nologin cloudsentinel-agent")  # noqa: S605
    os.system("chown cloudsentinel-agent /etc/cloudsentinel-agent.env")  # noqa: S605
    with open("/etc/systemd/system/cloudsentinel-agent.service", "w") as f:
        f.write(SERVICE)
    os.system("systemctl daemon-reload && systemctl enable --now cloudsentinel-agent")  # noqa: S605
    print("Installed and started cloudsentinel-agent.service")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CloudSentinel Linux host agent")
    p.add_argument("--url", default=os.environ.get("CLOUDSENTINEL_URL", ""))
    p.add_argument("--token", default=os.environ.get("CLOUDSENTINEL_TOKEN", ""))
    p.add_argument("--interval", type=float, default=float(os.environ.get("CLOUDSENTINEL_INTERVAL", "60")))
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--insecure", action="store_true", help="Skip TLS verification (testing only)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--print", dest="print_only", action="store_true")
    p.add_argument("--install", action="store_true")
    p.add_argument("--version", action="version", version=VERSION)
    args = p.parse_args(argv)

    if args.print_only:
        print(json.dumps(collect(), indent=2))
        return 0
    if args.install:
        return install(args.url, args.token)
    if not args.url or not args.token:
        p.error("--url/--token (or CLOUDSENTINEL_URL/CLOUDSENTINEL_TOKEN) are required")

    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    backoff = args.interval
    while running:
        try:
            status = send(args.url, args.token, collect(), args.timeout, args.insecure)
            print(f"{datetime.now(timezone.utc).isoformat()} report sent (HTTP {status})", flush=True)
            backoff = args.interval
            if args.once:
                return 0
        except urllib.error.HTTPError as exc:
            print(f"server rejected report: HTTP {exc.code} {exc.reason}", file=sys.stderr, flush=True)
            if exc.code == 401:
                print("the agent token is invalid or was revoked", file=sys.stderr)
            if args.once:
                return 1
        except (urllib.error.URLError, OSError) as exc:
            print(f"could not reach server: {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 1
            backoff = min(backoff * 2, 600)  # back off while the server is unreachable
        deadline = time.monotonic() + backoff
        while running and time.monotonic() < deadline:
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
