"""Operational reporting: availability, incident statistics and recurring problems, with CSV/PDF export."""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from cloudsentinel.models import (
    CheckResult,
    CheckStatus,
    Component,
    Incident,
    Monitor,
    RiskFinding,
    RiskStatus,
)


@dataclass
class MonitorAvailability:
    monitor_id: int
    monitor_name: str
    kind: str
    component: str | None
    checks: int
    failed: int
    availability_pct: float | None
    avg_latency_ms: float | None
    is_synthetic: bool


@dataclass
class ComponentAvailability:
    component_id: int
    component: str
    criticality: str
    availability_pct: float | None
    incidents: int
    downtime_minutes: float


@dataclass
class Report:
    start: datetime
    end: datetime
    generated_at: datetime
    overall_availability_pct: float | None
    total_checks: int
    incident_count: int
    resolved_incident_count: int
    mttr_minutes: float | None
    total_incident_minutes: float
    incidents_by_severity: dict[str, int]
    monitors: list[MonitorAvailability]
    components: list[ComponentAvailability]
    top_problems: list[dict[str, Any]]
    incidents: list[dict[str, Any]]
    open_risks: int
    contains_synthetic_data: bool
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _avail(checks: int, failed: int) -> float | None:
    return None if checks == 0 else round(100.0 * (checks - failed) / checks, 4)


def build_report(db: Session, start: datetime, end: datetime, now: datetime) -> Report:
    if end <= start:
        raise ValueError("end must be after start")
    is_failed = case((CheckResult.status == CheckStatus.down, 1), else_=0)
    rows = db.execute(
        select(
            CheckResult.monitor_id,
            func.count(CheckResult.id),
            func.sum(is_failed),
            func.avg(CheckResult.latency_ms),
        )
        .where(
            CheckResult.checked_at >= start,
            CheckResult.checked_at < end,
            CheckResult.status != CheckStatus.unknown,
        )
        .group_by(CheckResult.monitor_id)
    ).all()
    stats = {mid: (int(n), int(f or 0), avg) for mid, n, f, avg in rows}

    monitors: list[MonitorAvailability] = []
    comp_totals: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    synthetic = False
    for mon in db.scalars(select(Monitor).order_by(Monitor.name)):
        n, failed, avg = stats.get(mon.id, (0, 0, None))
        if n == 0 and not mon.enabled:
            continue
        synthetic = synthetic or (mon.is_synthetic and n > 0)
        monitors.append(
            MonitorAvailability(
                mon.id,
                mon.name,
                mon.kind,
                mon.component.name if mon.component else None,
                n,
                failed,
                _avail(n, failed),
                round(avg, 2) if avg is not None else None,
                mon.is_synthetic,
            )
        )
        if mon.component_id is not None:
            comp_totals[mon.component_id][0] += n
            comp_totals[mon.component_id][1] += failed

    incidents = db.scalars(
        select(Incident)
        .where(Incident.started_at < end, (Incident.resolved_at.is_(None)) | (Incident.resolved_at >= start))
        .order_by(Incident.started_at)
    ).all()

    def duration_min(i: Incident) -> float:
        s = max(i.started_at, start)
        e = min(i.resolved_at or now, end)
        return max(0.0, (e - s).total_seconds() / 60)

    resolved = [i for i in incidents if i.resolved_at is not None and i.resolved_at < end]
    mttr = (
        round(
            sum((i.resolved_at - i.started_at).total_seconds() for i in resolved if i.resolved_at)
            / 60
            / len(resolved),
            2,
        )
        if resolved
        else None
    )
    comp_incidents: Counter[int] = Counter(i.component_id for i in incidents if i.component_id)
    comp_downtime: dict[int, float] = defaultdict(float)
    for i in incidents:
        if i.component_id:
            comp_downtime[i.component_id] += duration_min(i)

    components = [
        ComponentAvailability(
            c.id,
            c.name,
            c.criticality,
            _avail(*comp_totals[c.id]) if c.id in comp_totals else None,
            comp_incidents.get(c.id, 0),
            round(comp_downtime.get(c.id, 0.0), 1),
        )
        for c in db.scalars(select(Component).order_by(Component.name))
    ]

    # Top recurring problems: monitors with the most incidents, then most failed checks.
    by_monitor: Counter[int] = Counter(i.monitor_id for i in incidents if i.monitor_id)
    names = {m.monitor_id: m for m in monitors}
    problems: list[dict[str, Any]] = []
    for mid, count in by_monitor.most_common(10):
        m = names.get(mid)
        problems.append(
            {
                "monitor_id": mid,
                "monitor": m.monitor_name if m else f"#{mid}",
                "component": m.component if m else None,
                "incidents": count,
                "failed_checks": m.failed if m else 0,
            }
        )
    if len(problems) < 10:
        seen = {p["monitor_id"] for p in problems}
        for m in sorted(monitors, key=lambda x: -x.failed):
            if m.failed and m.monitor_id not in seen and len(problems) < 10:
                problems.append(
                    {
                        "monitor_id": m.monitor_id,
                        "monitor": m.monitor_name,
                        "component": m.component,
                        "incidents": 0,
                        "failed_checks": m.failed,
                    }
                )

    total_checks = sum(m.checks for m in monitors)
    total_failed = sum(m.failed for m in monitors)
    synthetic = synthetic or any(i.is_synthetic for i in incidents)
    notes = []
    if synthetic:
        notes.append(
            "This report includes SYNTHETIC demo data generated by CloudSentinel's demo mode. "
            "It does not describe real infrastructure."
        )
    notes.append(
        "Availability = (checks that were up or degraded) / (checks with a verdict). "
        "Checks that could not reach a verdict (unknown) are excluded."
    )

    return Report(
        start=start,
        end=end,
        generated_at=now,
        overall_availability_pct=_avail(total_checks, total_failed),
        total_checks=total_checks,
        incident_count=len(incidents),
        resolved_incident_count=len(resolved),
        mttr_minutes=mttr,
        total_incident_minutes=round(sum(duration_min(i) for i in incidents), 1),
        incidents_by_severity=dict(Counter(i.severity for i in incidents)),
        monitors=monitors,
        components=components,
        top_problems=problems,
        incidents=[
            {
                "id": i.id,
                "title": i.title,
                "severity": i.severity,
                "status": i.status,
                "component": i.component.name if i.component else None,
                "started_at": i.started_at.isoformat(),
                "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
                "duration_minutes": round(duration_min(i), 1),
                "correlated_to": i.parent_id,
                "is_synthetic": i.is_synthetic,
            }
            for i in incidents
        ],
        open_risks=db.scalar(select(func.count(RiskFinding.id)).where(RiskFinding.status == RiskStatus.open))
        or 0,
        contains_synthetic_data=synthetic,
        notes=notes,
    )


def _fmt(v: float | None, suffix: str = "") -> str:
    return "n/a" if v is None else f"{v:g}{suffix}"


class _SafeWriter:
    """CSV writer that neutralises spreadsheet formula injection (cells starting with = + - @)."""

    def __init__(self, buf: io.StringIO) -> None:
        self._w = csv.writer(buf)

    def writerow(self, row: list[Any]) -> None:
        self._w.writerow([_safe_cell(v) for v in row])


def _safe_cell(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def report_csv(report: Report, section: str = "monitors") -> str:
    buf = io.StringIO()
    w = _SafeWriter(buf)
    if section == "incidents":
        w.writerow(
            [
                "id",
                "title",
                "severity",
                "status",
                "component",
                "started_at",
                "resolved_at",
                "duration_minutes",
                "correlated_to",
                "synthetic",
            ]
        )
        for i in report.incidents:
            w.writerow(
                [
                    i["id"],
                    i["title"],
                    i["severity"],
                    i["status"],
                    i["component"] or "",
                    i["started_at"],
                    i["resolved_at"] or "",
                    i["duration_minutes"],
                    i["correlated_to"] or "",
                    i["is_synthetic"],
                ]
            )
    elif section == "components":
        w.writerow(
            ["component_id", "component", "criticality", "availability_pct", "incidents", "downtime_minutes"]
        )
        for c in report.components:
            w.writerow(
                [
                    c.component_id,
                    c.component,
                    c.criticality,
                    _fmt(c.availability_pct),
                    c.incidents,
                    c.downtime_minutes,
                ]
            )
    else:
        w.writerow(
            [
                "monitor_id",
                "monitor",
                "kind",
                "component",
                "checks",
                "failed",
                "availability_pct",
                "avg_latency_ms",
                "synthetic",
            ]
        )
        for m in report.monitors:
            w.writerow(
                [
                    m.monitor_id,
                    m.monitor_name,
                    m.kind,
                    m.component or "",
                    m.checks,
                    m.failed,
                    _fmt(m.availability_pct),
                    _fmt(m.avg_latency_ms),
                    m.is_synthetic,
                ]
            )
    return buf.getvalue()


def report_pdf(report: Report) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="CloudSentinel infrastructure health report",
    )
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8, leading=10)
    story: list[Any] = []

    def table(data: list[list[Any]], widths: list[float] | None = None) -> Table:
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                    ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2933")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5f7")]),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d1d9")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return t

    story.append(Paragraph("CloudSentinel — Infrastructure Health Report", styles["Title"]))
    story.append(
        Paragraph(
            f"Period: {report.start:%Y-%m-%d %H:%M} to {report.end:%Y-%m-%d %H:%M} UTC · "
            f"Generated {report.generated_at:%Y-%m-%d %H:%M} UTC",
            small,
        )
    )
    if report.contains_synthetic_data:
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                "<b>SYNTHETIC DATA:</b> this report includes simulated demo data and does not "
                "describe real infrastructure.",
                styles["BodyText"],
            )
        )
    story.append(Spacer(1, 8))
    story.append(
        table(
            [
                [
                    "Overall availability",
                    "Checks",
                    "Incidents",
                    "Resolved",
                    "MTTR",
                    "Incident time",
                    "Open risks",
                ],
                [
                    _fmt(report.overall_availability_pct, "%"),
                    report.total_checks,
                    report.incident_count,
                    report.resolved_incident_count,
                    _fmt(report.mttr_minutes, " min"),
                    f"{report.total_incident_minutes:g} min",
                    report.open_risks,
                ],
            ]
        )
    )

    story.append(Spacer(1, 10))
    story.append(Paragraph("Service availability", styles["Heading2"]))
    rows: list[list[Any]] = [["Component", "Criticality", "Availability", "Incidents", "Downtime (min)"]]
    for c in sorted(report.components, key=lambda c: (c.availability_pct is None, c.availability_pct or 0)):
        rows.append(
            [
                Paragraph(c.component, small),
                c.criticality,
                _fmt(c.availability_pct, "%"),
                c.incidents,
                c.downtime_minutes,
            ]
        )
    story.append(table(rows, [60 * mm, 25 * mm, 30 * mm, 22 * mm, 30 * mm]))

    if report.top_problems:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Top recurring problems", styles["Heading2"]))
        rows = [["Monitor", "Component", "Incidents", "Failed checks"]]
        rows += [
            [
                Paragraph(p["monitor"], small),
                Paragraph(p["component"] or "—", small),
                p["incidents"],
                p["failed_checks"],
            ]
            for p in report.top_problems
        ]
        story.append(table(rows, [65 * mm, 55 * mm, 22 * mm, 28 * mm]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Incidents", styles["Heading2"]))
    if report.incidents:
        rows = [["#", "Title", "Severity", "Started (UTC)", "Duration", "Status"]]
        for i in report.incidents[:200]:
            rows.append(
                [
                    i["id"],
                    Paragraph(i["title"] + (" (synthetic)" if i["is_synthetic"] else ""), small),
                    i["severity"],
                    i["started_at"][:16].replace("T", " "),
                    f"{i['duration_minutes']:g} min",
                    i["status"],
                ]
            )
        story.append(table(rows, [12 * mm, 70 * mm, 20 * mm, 32 * mm, 22 * mm, 22 * mm]))
    else:
        story.append(Paragraph("No incidents in this period.", styles["BodyText"]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Monitor availability", styles["Heading2"]))
    rows = [["Monitor", "Type", "Checks", "Failed", "Availability", "Avg latency"]]
    for m in report.monitors:
        rows.append(
            [
                Paragraph(m.monitor_name, small),
                m.kind,
                m.checks,
                m.failed,
                _fmt(m.availability_pct, "%"),
                _fmt(m.avg_latency_ms, " ms"),
            ]
        )
    story.append(table(rows, [62 * mm, 22 * mm, 20 * mm, 18 * mm, 26 * mm, 26 * mm]))

    story.append(Spacer(1, 10))
    for note in report.notes:
        story.append(Paragraph(note, small))
    doc.build(story)
    return buf.getvalue()
