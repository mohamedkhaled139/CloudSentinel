import asyncio
import socket

from sqlalchemy import func, select

from cloudsentinel.db import session_factory
from cloudsentinel.models import CheckResult, Monitor
from cloudsentinel.services.scheduler import Scheduler


def test_scheduler_runs_due_monitors_and_periodic_jobs(db):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    port = sock.getsockname()[1]
    db.add(Monitor(name="tcp", kind="tcp", config={"host": "127.0.0.1", "port": port}, interval_seconds=10))
    db.add(Monitor(name="disabled", kind="tcp", config={"host": "127.0.0.1", "port": port}, enabled=False))
    db.add(Monitor(name="synthetic", kind="synthetic", config={}, interval_seconds=10))
    db.commit()
    calls = []

    async def job():
        calls.append(1)

    async def scenario():
        s = Scheduler(session_factory(), tick_seconds=0.05, periodic_jobs=[("job", 3600, job)])
        # Make the first run immediate instead of spread over the interval.
        s._spread_initial_runs = lambda: None
        s.start()
        for _ in range(100):
            await asyncio.sleep(0.05)
            with session_factory()() as check_db:
                if check_db.scalar(select(func.count(CheckResult.id))):
                    break
        await asyncio.sleep(0.1)
        healthy = s.healthy
        await s.stop()
        return healthy

    try:
        assert asyncio.run(scenario()) is True
    finally:
        sock.close()
    with session_factory()() as check_db:
        results = check_db.execute(
            select(Monitor.name, func.count(CheckResult.id)).join(CheckResult).group_by(Monitor.name)
        ).all()
    assert dict(results) == {"tcp": 1}  # once per interval; disabled and synthetic (non-demo) skipped
    assert calls == [1]
