"""
Analytics API — powers all 8 dashboard charts.

All data is pre-aggregated and PHI-free.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import require_admin, require_any_staff
from core.database import get_db
from models.appointment import Appointment
from models.call_log import CallLog

router = APIRouter(prefix="/analytics", tags=["analytics"])

_PKT = timezone(timedelta(hours=5))


def _date_range_utc(
    date_from: date | None,
    date_to: date | None,
) -> tuple[datetime, datetime]:
    end = (
        datetime.combine(date_to, datetime.max.time()).replace(tzinfo=_PKT).astimezone(timezone.utc)
        if date_to
        else datetime.now(timezone.utc)
    )
    start = (
        datetime.combine(date_from, datetime.min.time()).replace(tzinfo=_PKT).astimezone(timezone.utc)
        if date_from
        else end - timedelta(days=30)
    )
    return start, end


@router.get("/calls/volume")
async def call_volume(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """Daily call volume for the past N days, with zero-fill for empty days."""
    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    rows = await db.execute(
        select(
            func.date_trunc("day", CallLog.created_at).label("day"),
            func.count().label("total"),
        )
        .where(CallLog.created_at >= since.replace(hour=0, minute=0, second=0, microsecond=0))
        .group_by(text("day"))
        .order_by(text("day"))
    )
    counts: dict[str, int] = {}
    for r in rows:
        counts[str(r.day)[:10]] = r.total

    today = datetime.now(_PKT).date()
    labels: list[str] = []
    values: list[int] = []
    data: list[dict] = []
    for offset in range(days - 1, -1, -1):
        d = today - timedelta(days=offset)
        key = d.isoformat()
        labels.append(d.strftime("%a %d"))
        values.append(counts.get(key, 0))
        data.append({"day": key, "total": counts.get(key, 0)})
    return {"labels": labels, "values": values, "data": data}


@router.get("/calls/language-distribution")
async def language_distribution(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """Urdu / Punjabi / English breakdown — normalises locale codes (ur-PK, pa-PK)."""
    start, end = _date_range_utc(date_from, date_to)
    rows = await db.execute(
        select(CallLog.language, func.count().label("total"))
        .where(CallLog.created_at.between(start, end))
        .group_by(CallLog.language)
    )
    bucket = {"Urdu": 0, "Punjabi": 0, "English": 0}
    for r in rows:
        lang = (r.language or "").lower()
        if lang.startswith("ur"):
            bucket["Urdu"] += r.total
        elif lang.startswith("pa"):
            bucket["Punjabi"] += r.total
        elif lang.startswith("en"):
            bucket["English"] += r.total
    labels = ["Urdu", "Punjabi", "English"]
    values = [bucket[label] for label in labels]
    return {
        "labels": labels,
        "values": values,
        "data": [{"language": k, "total": v} for k, v in bucket.items()],
    }


@router.get("/calls/outcomes")
async def booking_outcomes(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """booked / escalated / abandoned / emergency counts."""
    start, end = _date_range_utc(date_from, date_to)
    rows = await db.execute(
        select(CallLog.outcome, func.count().label("total"))
        .where(CallLog.created_at.between(start, end))
        .group_by(CallLog.outcome)
    )
    return {"data": [{"outcome": r.outcome, "total": r.total} for r in rows]}


@router.get("/calls/latency")
async def provider_latency(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_admin)] = None,
) -> dict:
    """Per-provider average and p95 latency."""
    start, end = _date_range_utc(date_from, date_to)
    rows = await db.execute(
        select(
            CallLog.stt_provider,
            CallLog.llm_provider,
            CallLog.tts_provider,
            func.avg(CallLog.avg_stt_ms).label("avg_stt"),
            func.avg(CallLog.avg_llm_ms).label("avg_llm"),
            func.avg(CallLog.avg_tts_ms).label("avg_tts"),
            func.avg(CallLog.avg_total_ms).label("avg_total"),
            func.percentile_cont(0.95)
            .within_group(CallLog.avg_total_ms)
            .label("p95_total"),
        )
        .where(CallLog.created_at.between(start, end))
        .group_by(CallLog.stt_provider, CallLog.llm_provider, CallLog.tts_provider)
    )
    return {
        "data": [
            {
                "stt_provider": r.stt_provider,
                "llm_provider": r.llm_provider,
                "tts_provider": r.tts_provider,
                "avg_stt_ms": round(r.avg_stt or 0),
                "avg_llm_ms": round(r.avg_llm or 0),
                "avg_tts_ms": round(r.avg_tts or 0),
                "avg_total_ms": round(r.avg_total or 0),
                "p95_total_ms": round(r.p95_total or 0),
            }
            for r in rows
        ]
    }


@router.get("/calls/cost")
async def cost_breakdown(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_admin)] = None,
) -> dict:
    """Daily cost by component (STT + LLM + TTS + telephony)."""
    start, end = _date_range_utc(date_from, date_to)
    rows = await db.execute(
        select(
            func.date_trunc("day", CallLog.created_at).label("day"),
            func.sum(CallLog.cost_stt_usd).label("stt"),
            func.sum(CallLog.cost_llm_usd).label("llm"),
            func.sum(CallLog.cost_tts_usd).label("tts"),
            func.sum(CallLog.cost_telephony_usd).label("telephony"),
            func.sum(CallLog.cost_total_usd).label("total"),
        )
        .where(CallLog.created_at.between(start, end))
        .group_by(text("day"))
        .order_by(text("day"))
    )
    return {
        "data": [
            {
                "day": str(r.day)[:10],
                "stt_usd": round(r.stt or 0, 4),
                "llm_usd": round(r.llm or 0, 4),
                "tts_usd": round(r.tts or 0, 4),
                "telephony_usd": round(r.telephony or 0, 4),
                "total_usd": round(r.total or 0, 4),
            }
            for r in rows
        ]
    }


@router.get("/calls/peak-hours")
async def peak_hours(
    days: int = Query(default=30, ge=7, le=90),
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """Call volume heatmap: hour of day × day of week (PKT)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    # Extract PKT hour and weekday — PKT = UTC+5
    rows = await db.execute(
        select(
            func.extract("dow", CallLog.created_at + text("interval '5 hours'")).label("weekday"),
            func.extract("hour", CallLog.created_at + text("interval '5 hours'")).label("hour"),
            func.count().label("count"),
        )
        .where(CallLog.created_at >= since)
        .group_by(text("weekday"), text("hour"))
        .order_by(text("weekday"), text("hour"))
    )
    return {
        "data": [
            {"weekday": int(r.weekday), "hour": int(r.hour), "count": r.count}
            for r in rows
        ]
    }


@router.get("/calls/escalations")
async def escalation_reasons(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """Escalation reason breakdown."""
    start, end = _date_range_utc(date_from, date_to)
    rows = await db.execute(
        select(CallLog.escalation_reason, func.count().label("total"))
        .where(
            CallLog.created_at.between(start, end),
            CallLog.escalation_triggered.is_(True),
        )
        .group_by(CallLog.escalation_reason)
    )
    return {"data": [{"reason": r.escalation_reason or "unknown", "total": r.total} for r in rows]}


@router.get("/summary")
async def daily_summary(
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """Today's KPI summary for dashboard stat cards (PKT day)."""
    now_pkt = datetime.now(_PKT)
    today_start = datetime(now_pkt.year, now_pkt.month, now_pkt.day, tzinfo=_PKT).astimezone(timezone.utc)
    today_end = today_start + timedelta(days=1)

    call_row = (await db.execute(
        select(
            func.count().label("total_calls"),
            func.sum(case((CallLog.outcome == "booked", 1), else_=0)).label("booked"),
            func.sum(case((CallLog.escalation_triggered.is_(True), 1), else_=0)).label("escalations"),
            func.sum(CallLog.cost_total_usd).label("cost_today"),
        ).where(CallLog.created_at.between(today_start, today_end))
    )).one()

    appts_today = (await db.execute(
        select(func.count())
        .select_from(Appointment)
        .where(Appointment.slot_start_utc.between(today_start, today_end))
    )).scalar_one()

    appts_booked_today = (await db.execute(
        select(func.count())
        .select_from(Appointment)
        .where(
            Appointment.slot_start_utc.between(today_start, today_end),
            Appointment.status.in_(("scheduled", "confirmed", "completed")),
        )
    )).scalar_one()

    cost_today_usd = round(call_row.cost_today or 0, 4)
    return {
        # Existing keys (kept for backwards compatibility)
        "total_calls": call_row.total_calls or 0,
        "booked": (call_row.booked or 0) + (appts_booked_today or 0),
        "escalations": call_row.escalations or 0,
        "cost_today_usd": cost_today_usd,
        # Keys the dashboard reads
        "todays_appointments": appts_today or 0,
        "cost_today": f"{cost_today_usd:.2f}",
        "system_ok": True,
        "active_calls": 0,  # live count comes from WebSocket; keep placeholder
    }
