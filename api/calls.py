from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_user, require_any_staff
from core.database import get_db
from models.audit import AuditLog
from models.auth import User
from models.call_log import CallLog, Transcript
from providers.telephony.session_manager import session_manager

router = APIRouter(prefix="/calls", tags=["calls"])

_PHI_ROLES = {"admin", "doctor"}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class CallLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    language: str
    intent: Optional[str]
    outcome: str
    stt_provider: str
    llm_provider: str
    tts_provider: str
    avg_stt_ms: Optional[int]
    avg_llm_ms: Optional[int]
    avg_tts_ms: Optional[int]
    avg_total_ms: Optional[int]
    cost_total_usd: Optional[float]
    escalation_triggered: bool
    emergency_detected: bool
    duration_seconds: Optional[int]
    turn_count: int
    created_at: datetime


class TranscriptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    call_log_id: int
    turn_id: int
    speaker: str
    language: str
    is_rtl: bool
    masked_text: Optional[str]
    stt_confidence: Optional[float]
    created_at: datetime


class TranscriptWithRawResponse(TranscriptResponse):
    raw_text: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _audit_phi_access(
    db: AsyncSession,
    *,
    user: User,
    request: Request,
    entity_id: int,
    notes: str,
) -> None:
    log = AuditLog(
        created_at=datetime.now(timezone.utc),
        user_id=user.id,
        user_email=user.email,
        ip_address=request.client.host if request.client else None,
        action="PHI_ACCESS",
        entity_type="transcript",
        entity_id=entity_id,
        notes=notes,
    )
    db.add(log)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/live")
async def list_live_calls(
    current_user: User = Depends(require_any_staff),
) -> list:
    return await session_manager.get_all_active()


_CALL_SORT_OPTIONS = {
    "recent": CallLog.created_at.desc(),
    "oldest": CallLog.created_at.asc(),
    "duration_desc": CallLog.duration_seconds.desc().nulls_last(),
    "duration_asc": CallLog.duration_seconds.asc().nulls_last(),
    "cost_desc": CallLog.cost_total_usd.desc().nulls_last(),
}


@router.get("/history")
async def list_call_history(
    db: Annotated[AsyncSession, Depends(get_db)],
    language: Optional[str] = Query(default=None),
    outcome: Optional[str] = Query(default=None),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    escalation_only: bool = Query(default=False),
    escalations_only: bool = Query(default=False),
    sort: str = Query(default="recent"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=200),
    current_user: User = Depends(require_any_staff),
) -> dict:
    filters = []
    if language is not None:
        filters.append(CallLog.language == language)
    if outcome is not None:
        filters.append(CallLog.outcome == outcome)
    if date_from is not None:
        filters.append(CallLog.created_at >= datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc))
    if date_to is not None:
        dt_to = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
        filters.append(CallLog.created_at <= dt_to)
    if escalation_only or escalations_only:
        filters.append(CallLog.escalation_triggered.is_(True))

    base_stmt = select(CallLog)
    count_stmt = select(func.count()).select_from(CallLog)
    if filters:
        base_stmt = base_stmt.where(and_(*filters))
        count_stmt = count_stmt.where(and_(*filters))

    order_clause = _CALL_SORT_OPTIONS.get(sort, _CALL_SORT_OPTIONS["recent"])
    base_stmt = base_stmt.order_by(order_clause).offset((page - 1) * page_size).limit(page_size)

    total = (await db.execute(count_stmt)).scalar_one()
    logs = (await db.execute(base_stmt)).scalars().all()

    items = []
    for c in logs:
        item = CallLogResponse.model_validate(c).model_dump()
        # Friendly aliases the UI table reads.
        item["escalated"] = c.escalation_triggered
        item["total_cost"] = c.cost_total_usd
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort": sort,
    }


@router.get("/session/{session_id}")
async def get_call_by_session(
    session_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: User = Depends(require_any_staff),
) -> dict:
    """
    Full call detail keyed by session UUID — used by the call-history detail modal.
    Returns the shape the UI consumes: nested cost_breakdown, latency_metrics, transcript[].
    """
    call_result = await db.execute(select(CallLog).where(CallLog.session_id == session_id))
    call = call_result.scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found.")

    turns_result = await db.execute(
        select(Transcript)
        .where(Transcript.call_log_id == call.id)
        .order_by(Transcript.turn_id.asc())
    )
    turns = turns_result.scalars().all()

    include_raw = current_user.role in _PHI_ROLES
    if include_raw and turns:
        await _audit_phi_access(
            db,
            user=current_user,
            request=request,
            entity_id=call.id,
            notes=f"Transcript accessed for session={session_id}, {len(turns)} turns",
        )
        await db.commit()

    transcript = [
        {
            "turn_id": t.turn_id,
            "role": t.speaker,  # frontend reads `role`
            "text": (t.raw_text if include_raw else t.masked_text) or t.masked_text or "",
            "language": t.language,
            "is_rtl": t.is_rtl,
            "stt_confidence": t.stt_confidence,
        }
        for t in turns
    ]

    return {
        "id": call.id,
        "session_id": call.session_id,
        "language": call.language,
        "intent": call.intent,
        "outcome": call.outcome,
        "duration_seconds": call.duration_seconds,
        "turn_count": call.turn_count,
        "stt_provider": call.stt_provider,
        "llm_provider": call.llm_provider,
        "tts_provider": call.tts_provider,
        "llm_model": call.llm_model,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "created_at": call.created_at.isoformat(),
        "escalated": call.escalation_triggered,
        "emergency_detected": call.emergency_detected,
        "total_cost": call.cost_total_usd,
        "cost_breakdown": {
            "stt": call.cost_stt_usd or 0,
            "llm": call.cost_llm_usd or 0,
            "tts": call.cost_tts_usd or 0,
            "telephony": call.cost_telephony_usd or 0,
            "total": call.cost_total_usd or 0,
        },
        "latency_metrics": {
            "avg_stt_ms": call.avg_stt_ms or 0,
            "avg_llm_ms": call.avg_llm_ms or 0,
            "avg_tts_ms": call.avg_tts_ms or 0,
            "avg_total_ms": call.avg_total_ms or 0,
            "p95_total_ms": call.p95_total_ms or 0,
        },
        "transcript": transcript,
    }


@router.get("/{call_id}", response_model=CallLogResponse)
async def get_call(
    call_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: User = Depends(require_any_staff),
) -> CallLogResponse:
    result = await db.execute(select(CallLog).where(CallLog.id == call_id))
    call = result.scalar_one_or_none()
    if call is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Call log not found.",
        )
    return CallLogResponse.model_validate(call)


@router.get("/{call_id}/transcript")
async def get_transcript(
    call_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: User = Depends(require_any_staff),
) -> list:
    # Verify the call exists
    call_result = await db.execute(select(CallLog).where(CallLog.id == call_id))
    call = call_result.scalar_one_or_none()
    if call is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Call log not found.",
        )

    result = await db.execute(
        select(Transcript)
        .where(Transcript.call_log_id == call_id)
        .order_by(Transcript.turn_id.asc())
    )
    turns = result.scalars().all()

    include_raw = current_user.role in _PHI_ROLES

    if include_raw and turns:
        # Log PHI access for this transcript
        await _audit_phi_access(
            db,
            user=current_user,
            request=request,
            entity_id=call_id,
            notes=f"Transcript raw_text accessed for call_id={call_id}, {len(turns)} turns",
        )
        await db.commit()

        return [
            {
                "id": t.id,
                "call_log_id": t.call_log_id,
                "turn_id": t.turn_id,
                "speaker": t.speaker,
                "language": t.language,
                "is_rtl": t.is_rtl,
                "raw_text": t.raw_text,
                "masked_text": t.masked_text,
                "stt_confidence": t.stt_confidence,
                "created_at": t.created_at,
            }
            for t in turns
        ]

    return [
        {
            "id": t.id,
            "call_log_id": t.call_log_id,
            "turn_id": t.turn_id,
            "speaker": t.speaker,
            "language": t.language,
            "is_rtl": t.is_rtl,
            "masked_text": t.masked_text,
            "stt_confidence": t.stt_confidence,
            "created_at": t.created_at,
        }
        for t in turns
    ]


@router.delete("/{call_id}", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
async def delete_call(
    call_id: int,
    current_user: User = Depends(require_any_staff),
) -> None:
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Call logs cannot be deleted per data retention policy",
    )
