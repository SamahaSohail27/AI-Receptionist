"""
Scheduling API — slot availability queries and booking via voice-intent parameters.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import require_any_staff
from core.database import get_db

router = APIRouter(prefix="/scheduling", tags=["scheduling"])


class SlotResponse(BaseModel):
    doctor_id: int
    start_utc: str
    end_utc: str
    duration_minutes: int
    is_available: bool


class BookingRequest(BaseModel):
    patient_id: int
    doctor_id: int
    slot_start_utc: datetime
    appointment_type: str = "consultation"
    booking_source: str = "ai"
    notes: str | None = None
    idempotency_key: str | None = None
    session_id: str | None = None


class VoiceBookingRequest(BaseModel):
    """Booking request as parsed from voice — includes raw language input for date parsing."""
    patient_id: int
    doctor_id: int | None = None
    speciality: str | None = None
    date_expression: str = ""
    language: str = "ur-PK"
    appointment_type: str = "consultation"
    session_id: str = ""


@router.get("/availability/{doctor_id}")
async def get_doctor_availability(
    doctor_id: int,
    from_date: date = Query(default_factory=lambda: date.today()),
    to_date: date = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """Return available slots for a doctor in a date range."""
    if to_date is None:
        to_date = from_date + timedelta(days=7)
    if (to_date - from_date).days > 30:
        raise HTTPException(status_code=422, detail="Date range must be <= 30 days")

    from scheduling.availability import AvailabilityEngine
    engine = AvailabilityEngine(db)
    slots = await engine.get_available_slots(doctor_id, from_date, to_date)
    return {
        "doctor_id": doctor_id,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "slots": [
            {
                "start_utc": s.start_utc.isoformat(),
                "end_utc": s.end_utc.isoformat(),
                "duration_minutes": s.duration_minutes,
                "is_available": s.is_available,
            }
            for s in slots
            if s.is_available
        ],
    }


@router.post("/book")
async def book_appointment(
    body: BookingRequest,
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """Book a specific slot directly (used by UI and pipeline tool calls)."""
    import redis.asyncio as aioredis
    from core.config import settings
    from scheduling.engine import BookingService, ConflictDetector

    redis_client = aioredis.from_url(settings.redis_url)
    service = BookingService(db, redis_client)
    detector = ConflictDetector()

    conflict = await detector.has_conflict(body.doctor_id, body.slot_start_utc, db)
    if conflict:
        raise HTTPException(status_code=409, detail="Slot already booked")

    appointment = await service.book_appointment(
        patient_id=body.patient_id,
        doctor_id=body.doctor_id,
        slot_start_utc=body.slot_start_utc,
        appointment_type=body.appointment_type,
        booking_source=body.booking_source,
        notes=body.notes,
        idempotency_key=body.idempotency_key,
    )
    if appointment is None:
        raise HTTPException(status_code=409, detail="Slot already booked (conflict at commit time)")

    return {
        "appointment_id": appointment.id,
        "status": "booked",
        "slot_start_utc": appointment.slot_start_utc.isoformat(),
        "slot_end_utc": appointment.slot_end_utc.isoformat(),
    }


@router.post("/book/voice")
async def book_from_voice_intent(
    body: VoiceBookingRequest,
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """
    Book an appointment from voice-parsed parameters.
    Parses date_expression in the given language, finds a slot, and books it.
    """
    from scheduling.date_parser import MultiLanguageDateParser
    from scheduling.availability import AvailabilityEngine
    from scheduling.pkt_calendar import PKTCalendar
    import redis.asyncio as aioredis
    from core.config import settings
    from scheduling.engine import BookingService

    calendar = PKTCalendar()
    parser = MultiLanguageDateParser(calendar)

    parsed_dt = parser.parse(body.date_expression, body.language)
    if parsed_dt is None:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse date expression: {body.date_expression!r} in language {body.language}",
        )

    from_date = parsed_dt.date()
    engine = AvailabilityEngine(db, calendar)
    slots = await engine.get_available_slots(body.doctor_id, from_date, from_date + timedelta(days=1))
    available = [s for s in slots if s.is_available]
    if not available:
        raise HTTPException(status_code=404, detail="No available slots on that date")

    chosen = available[0]
    redis_client = aioredis.from_url(settings.redis_url)
    service = BookingService(db, redis_client)

    idempotency_key = f"{body.session_id}:{body.doctor_id}:{chosen.start_utc.isoformat()}" if body.session_id else None
    appointment = await service.book_appointment(
        patient_id=body.patient_id,
        doctor_id=body.doctor_id,
        slot_start_utc=chosen.start_utc,
        appointment_type=body.appointment_type,
        booking_source="ai",
        idempotency_key=idempotency_key,
    )
    if appointment is None:
        raise HTTPException(status_code=409, detail="Slot taken — please try another time")

    return {
        "appointment_id": appointment.id,
        "status": "booked",
        "slot_start_utc": appointment.slot_start_utc.isoformat(),
        "slot_end_utc": appointment.slot_end_utc.isoformat(),
        "parsed_date_expression": body.date_expression,
        "parsed_pkt_datetime": parsed_dt.isoformat(),
    }
