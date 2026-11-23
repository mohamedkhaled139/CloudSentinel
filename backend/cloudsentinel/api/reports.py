"""Operational reports with CSV and PDF export."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from cloudsentinel.db import get_db
from cloudsentinel.deps import require_viewer
from cloudsentinel.models import User, utcnow
from cloudsentinel.schemas import ReportSection
from cloudsentinel.services.reports import Report, build_report, report_csv, report_pdf

router = APIRouter(tags=["reports"])
MAX_RANGE = timedelta(days=366)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    end = _aware(end) if end else utcnow()
    start = _aware(start) if start else end - timedelta(days=7)
    if end <= start:
        raise HTTPException(422, "end must be after start")
    if end - start > MAX_RANGE:
        raise HTTPException(422, "Report range is limited to 366 days")
    return start, end


def _build(db: Session, start: datetime | None, end: datetime | None) -> Report:
    s, e = _range(start, end)
    return build_report(db, s, e, utcnow())


@router.get("/reports/summary")
def summary(
    start: datetime | None = None,
    end: datetime | None = None,
    _: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _build(db, start, end).as_dict()


def _filename(report: Report, ext: str, section: str = "") -> str:
    suffix = f"-{section}" if section else ""
    return f"cloudsentinel-report{suffix}-{report.start:%Y%m%d}-{report.end:%Y%m%d}.{ext}"


@router.get("/reports/export.csv")
def export_csv(
    start: datetime | None = None,
    end: datetime | None = None,
    section: ReportSection = Query(default="monitors"),
    _: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Response:
    report = _build(db, start, end)
    return Response(
        report_csv(report, section),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_filename(report, "csv", section)}"'},
    )


@router.get("/reports/export.pdf")
async def export_pdf(
    start: datetime | None = None,
    end: datetime | None = None,
    _: User = Depends(require_viewer),
    db: Session = Depends(get_db),
) -> Response:
    report = _build(db, start, end)
    pdf = await asyncio.to_thread(report_pdf, report)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_filename(report, "pdf")}"'},
    )
