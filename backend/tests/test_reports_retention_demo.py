import csv
import io
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from cloudsentinel.models import CheckResult, Component, Incident, Monitor, RiskFinding
from cloudsentinel.services.demo import clear_demo_data, seed_demo
from cloudsentinel.services.reports import build_report, report_csv, report_pdf
from cloudsentinel.services.retention import apply_retention
from cloudsentinel.services.settings_store import update_app_settings
from tests.helpers import component, feed, monitor

T0 = datetime(2026, 2, 1, tzinfo=UTC)


def test_report_availability_and_incidents(db):
    c = component(db, "api", criticality="high")
    m = monitor(db, "api-health", c, failure_threshold=2, recovery_threshold=1)
    feed(db, m, "uuuuuuddduu", T0)  # 11 checks, 3 failed
    other = monitor(db, "other", component(db, "other"))
    feed(db, other, "xxxx", T0)  # unknown results are excluded
    r = build_report(db, T0, T0 + timedelta(hours=1), T0 + timedelta(hours=2))
    mon = next(x for x in r.monitors if x.monitor_id == m.id)
    assert mon.checks == 11 and mon.failed == 3
    assert mon.availability_pct == round(100 * 8 / 11, 4)
    assert next(x for x in r.monitors if x.monitor_id == other.id).availability_pct is None
    assert r.incident_count == 1 and r.resolved_incident_count == 1
    assert r.mttr_minutes == 3.0  # failing from minute 6, resolved at minute 9
    assert r.top_problems[0]["monitor_id"] == m.id
    assert not r.contains_synthetic_data

    rows = list(csv.reader(io.StringIO(report_csv(r, "monitors"))))
    assert rows[0][0] == "monitor_id" and len(rows) == 3
    inc_rows = list(csv.reader(io.StringIO(report_csv(r, "incidents"))))
    assert inc_rows[1][1] == "api is unavailable"
    pdf = report_pdf(r)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


def test_report_range_excludes_outside_data(db):
    m = monitor(db, "m", component(db, "c"))
    feed(db, m, "uuuu", T0)
    r = build_report(db, T0 + timedelta(days=1), T0 + timedelta(days=2), T0 + timedelta(days=3))
    assert r.total_checks == 0 and r.overall_availability_pct is None


def test_retention(db):
    update_app_settings(db, {"retention_check_results_days": 1})
    m = monitor(db, "m", component(db, "c"))
    feed(db, m, "uuu", T0)
    feed(db, m, "uu", T0 + timedelta(days=5))
    out = apply_retention(db, now=T0 + timedelta(days=5, hours=1))
    assert out["check_results"] == 3
    assert db.scalar(select(func.count(CheckResult.id))) == 2


def test_demo_seed_is_flagged_synthetic_and_clearable(db):
    real = component(db, "real-service")
    stats = seed_demo(db, days=3, step_minutes=5)
    assert stats["components"] == 13 and stats["incidents"] > 0
    assert db.scalar(select(func.count(Monitor.id)).where(Monitor.is_synthetic.is_(False))) == 0
    assert all(i.is_synthetic for i in db.scalars(select(Incident)))
    assert all(r.is_synthetic for r in db.scalars(select(CheckResult)))
    # correlated incidents exist (redis outage cascades to dependents)
    assert db.scalar(select(func.count(Incident.id)).where(Incident.parent_id.is_not(None))) > 0
    report = build_report(db, datetime.now(UTC) - timedelta(days=3), datetime.now(UTC), datetime.now(UTC))
    assert report.contains_synthetic_data and "SYNTHETIC" in report.notes[0]
    clear_demo_data(db)
    assert db.scalars(select(Component)).all() == [real]
    assert db.scalar(select(func.count(Incident.id))) == 0
    assert db.scalar(select(func.count(RiskFinding.id)).where(RiskFinding.is_synthetic.is_(True))) == 0


def test_csv_export_neutralises_formulas(db):
    m = monitor(db, '=HYPERLINK("http://evil")', component(db, "@cmd"))
    feed(db, m, "u", T0)
    r = build_report(db, T0, T0 + timedelta(hours=1), T0 + timedelta(hours=1))
    rows = list(csv.reader(io.StringIO(report_csv(r, "monitors"))))
    assert rows[1][1].startswith("'=") and rows[1][3] == "'@cmd"
