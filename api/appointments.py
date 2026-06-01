from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.auth import require_any_staff
from core.database import get_db
from models.appointment import Appointment
from models.audit import AuditLog
from models.auth import User
from models.doctor import Doctor
from models.patient import Patient

router = APIRouter(prefix="/appointments", tags=["appointments"])

# Pakistan Standard Time is UTC+5, no DST
_PKT_OFFSET = timedelta(hours=5)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class AppointmentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    patient_id: int
    doctor_id: int
    slot_start_utc: datetime
    appointment_type: str = "consultation"
    booking_source: str = "manual"
    notes: Optional[str] = None
    idempotency_key: Optional[str] = None


class AppointmentUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: Optional[str] = None
    notes: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    cancelled_by: Optional[str] = None


class AppointmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    doctor_id: int
    slot_start_utc: datetime
    slot_end_utc: datetime
    status: str
    appointment_type: str
    booking_source: str
    idempotency_key: Optional[str]
    reminder_24h_sent: bool
    reminder_2h_sent: bool
    whatsapp_confirmation_sent: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Helper: AuditLog creation
# ---------------------------------------------------------------------------

async def _audit(
    db: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: Optional[int],
    user: User,
    request: Request,
    notes: Optional[str] = None,
) -> None:
    log = AuditLog(
        created_at=datetime.now(timezone.utc),
        user_id=user.id,
        user_email=user.email,
        ip_address=request.client.host if request.client else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        notes=notes,
    )
    db.add(log)


# ---------------------------------------------------------------------------
# Helper: check doctor-slot conflict
# ---------------------------------------------------------------------------

async def _check_conflict(
    db: AsyncSession,
    doctor_id: int,
    slot_start_utc: datetime,
    exclude_appointment_id: Optional[int] = None,
) -> bool:
    """Return True if a conflicting active appointment exists."""
    stmt = select(Appointment.id).where(
        and_(
            Appointment.doctor_id == doctor_id,
            Appointment.slot_start_utc == slot_start_utc,
            Appointment.status != "cancelled",
        )
    )
    if exclude_appointment_id is not None:
        stmt = stmt.where(Appointment.id != exclude_appointment_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Routes — NOTE: /today must be declared before /{appointment_id} so FastAPI
# does not try to coerce "today" to an int.
# ---------------------------------------------------------------------------

@router.get("/today", response_model=list[AppointmentResponse])
async def list_today_appointments(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> list[AppointmentResponse]:
    """Return all appointments for today in PKT (UTC+5)."""
    now_utc = datetime.now(timezone.utc)
    # Today's date in PKT
    now_pkt = now_utc + _PKT_OFFSET
    today_pkt = now_pkt.date()

    # Convert PKT midnight and PKT end-of-day to UTC for the query
    pkt_start = datetime(today_pkt.year, today_pkt.month, today_pkt.day,
                         tzinfo=timezone.utc) - _PKT_OFFSET
    pkt_end = pkt_start + timedelta(days=1)

    stmt = (
        select(Appointment)
        .where(
            and_(
                Appointment.slot_start_utc >= pkt_start,
                Appointment.slot_start_utc < pkt_end,
            )
        )
        .order_by(Appointment.slot_start_utc)
    )
    result = await db.execute(stmt)
    appointments = result.scalars().all()
    return [AppointmentResponse.model_validate(a) for a in appointments]


@router.get("", response_model=list[AppointmentResponse])
async def list_appointments(
    doctor_id: Optional[int] = Query(default=None),
    patient_id: Optional[int] = Query(default=None),
    appointment_status: Optional[str] = Query(default=None, alias="status"),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> list[AppointmentResponse]:
    stmt = select(Appointment)

    if doctor_id is not None:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)
    if patient_id is not None:
        stmt = stmt.where(Appointment.patient_id == patient_id)
    if appointment_status:
        stmt = stmt.where(Appointment.status == appointment_status)
    if date_from:
        # date_from is a PKT date — convert to UTC lower bound
        dt_from_utc = datetime(date_from.year, date_from.month, date_from.day,
                               tzinfo=timezone.utc) - _PKT_OFFSET
        stmt = stmt.where(Appointment.slot_start_utc >= dt_from_utc)
    if date_to:
        # date_to inclusive — take end of that PKT day
        dt_to_utc = (
            datetime(date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc)
            - _PKT_OFFSET
            + timedelta(days=1)
        )
        stmt = stmt.where(Appointment.slot_start_utc < dt_to_utc)

    stmt = (
        stmt.order_by(Appointment.slot_start_utc)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    appointments = result.scalars().all()
    return [AppointmentResponse.model_validate(a) for a in appointments]


@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    request: Request,
    body: AppointmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> AppointmentResponse:
    # Idempotency: if key provided and record already exists, return it
    if body.idempotency_key:
        existing = await db.execute(
            select(Appointment).where(Appointment.idempotency_key == body.idempotency_key)
        )
        existing_appt = existing.scalar_one_or_none()
        if existing_appt is not None:
            return AppointmentResponse.model_validate(existing_appt)

    # Fetch doctor to derive slot_end_utc
    doc_result = await db.execute(select(Doctor).where(Doctor.id == body.doctor_id))
    doctor = doc_result.scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found.")

    # Ensure slot_start_utc is timezone-aware
    slot_start = body.slot_start_utc
    if slot_start.tzinfo is None:
        slot_start = slot_start.replace(tzinfo=timezone.utc)

    # Conflict check
    if await _check_conflict(db, body.doctor_id, slot_start):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The requested time slot is already booked for this doctor.",
        )

    slot_end = slot_start + timedelta(minutes=doctor.consultation_duration_minutes)

    appointment = Appointment(
        patient_id=body.patient_id,
        doctor_id=body.doctor_id,
        slot_start_utc=slot_start,
        slot_end_utc=slot_end,
        appointment_type=body.appointment_type,
        booking_source=body.booking_source,
        notes=body.notes,
        idempotency_key=body.idempotency_key,
        status="scheduled",
    )
    db.add(appointment)
    await db.flush()

    await _audit(
        db,
        action="APPOINTMENT_BOOK",
        entity_type="appointment",
        entity_id=appointment.id,
        user=current_user,
        request=request,
        notes=f"Appointment booked for doctor_id={body.doctor_id}",
    )

    await db.commit()
    await db.refresh(appointment)
    return AppointmentResponse.model_validate(appointment)


_APPT_SORT_OPTIONS = {
    "upcoming": Appointment.slot_start_utc.asc(),
    "recent": Appointment.slot_start_utc.desc(),
    "created_desc": Appointment.created_at.desc(),
    "created_asc": Appointment.created_at.asc(),
}


@router.get("/page")
async def list_appointments_page(
    db: AsyncSession = Depends(get_db),
    doctor_id: Optional[int] = Query(default=None),
    appointment_status: Optional[str] = Query(default=None, alias="status"),
    appointment_type: Optional[str] = Query(default=None),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    sort: str = Query(default="upcoming"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=200),
    current_user: User = Depends(require_any_staff),
) -> dict:
    filters = []
    if doctor_id is not None:
        filters.append(Appointment.doctor_id == doctor_id)
    if appointment_status:
        filters.append(Appointment.status == appointment_status)
    if appointment_type:
        filters.append(Appointment.appointment_type == appointment_type)
    if date_from:
        dt_from_utc = datetime(date_from.year, date_from.month, date_from.day,
                               tzinfo=timezone.utc) - _PKT_OFFSET
        filters.append(Appointment.slot_start_utc >= dt_from_utc)
    if date_to:
        dt_to_utc = (
            datetime(date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc)
            - _PKT_OFFSET + timedelta(days=1)
        )
        filters.append(Appointment.slot_start_utc < dt_to_utc)

    base_stmt = (
        select(Appointment)
        .options(selectinload(Appointment.doctor), selectinload(Appointment.patient))
    )
    count_stmt = select(func.count()).select_from(Appointment)
    if filters:
        base_stmt = base_stmt.where(and_(*filters))
        count_stmt = count_stmt.where(and_(*filters))

    order_clause = _APPT_SORT_OPTIONS.get(sort, _APPT_SORT_OPTIONS["upcoming"])
    base_stmt = base_stmt.order_by(order_clause).offset((page - 1) * page_size).limit(page_size)

    total = (await db.execute(count_stmt)).scalar_one()
    appts = (await db.execute(base_stmt)).scalars().all()

    items = []
    for a in appts:
        doc = a.doctor
        pat = a.patient
        items.append({
            "id": a.id,
            "slot_start_utc": a.slot_start_utc.isoformat(),
            "slot_end_utc": a.slot_end_utc.isoformat(),
            "status": a.status,
            "appointment_type": a.appointment_type,
            "booking_source": a.booking_source,
            "notes": a.notes,
            "doctor_id": a.doctor_id,
            "doctor_name": doc.name_en if doc else None,
            "doctor_name_ur": doc.name_ur if doc else None,
            "doctor_color": doc.calendar_color if doc else None,
            "doctor_specialty": doc.speciality if doc else None,
            "doctor_room": doc.room_number if doc else None,
            "doctor_fee": doc.consultation_fee if doc else None,
            "patient_id": a.patient_id,
            "patient_name": (pat.name_en or pat.name_ur) if pat else None,
            "patient_name_ur": pat.name_ur if pat else None,
            "patient_phone": pat.phone_e164 if pat else None,
            "patient_gender": pat.gender if pat else None,
            "patient_birth_year": pat.birth_year if pat else None,
            "patient_language": pat.preferred_language if pat else None,
            "patient_whatsapp": pat.whatsapp_opted_in if pat else None,
            "created_at": a.created_at.isoformat(),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort": sort,
    }


@router.get("/calendar")
async def calendar_events(
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    doctor_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> list[dict]:
    """FullCalendar event source — returns events shaped for FullCalendar."""
    stmt = (
        select(Appointment)
        .options(selectinload(Appointment.doctor), selectinload(Appointment.patient))
        .where(Appointment.status != "cancelled")
    )
    if doctor_id is not None:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)
    if start is not None:
        stmt = stmt.where(Appointment.slot_start_utc >= start)
    if end is not None:
        stmt = stmt.where(Appointment.slot_start_utc < end)

    result = await db.execute(stmt.order_by(Appointment.slot_start_utc))
    appts = result.scalars().all()

    events = []
    for a in appts:
        doc = a.doctor
        pat = a.patient
        title_name = (pat.name_en or pat.name_ur or "Patient") if pat else "Patient"
        title = f"{title_name} — {doc.name_en}" if doc else title_name
        events.append({
            "id": str(a.id),
            "title": title,
            "start": a.slot_start_utc.isoformat(),
            "end": a.slot_end_utc.isoformat(),
            "color": (doc.calendar_color if doc else "#0EA5E9"),
            "extendedProps": {
                "source": a.booking_source,
                "appointment_type": a.appointment_type,
                "doctor_name": doc.name_en if doc else None,
                "patient_phone": pat.phone_e164 if pat else None,
                "status": a.status,
            },
        })
    return events


@router.post("/{appointment_id}/cancel", response_model=AppointmentResponse)
async def cancel_appointment_post(
    appointment_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> AppointmentResponse:
    """Alias for the DELETE cancel endpoint — frontend uses POST /cancel."""
    return await cancel_appointment(appointment_id, request, db, current_user)


@router.get("/day-sheet", response_class=HTMLResponse)
async def day_sheet(
    date: Optional[date] = Query(default=None),
    doctor_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> HTMLResponse:
    """Printable day sheet — simple HTML rendering of one day's appointments."""
    target = date or (datetime.now(timezone.utc) + _PKT_OFFSET).date()
    pkt_start = datetime(target.year, target.month, target.day, tzinfo=timezone.utc) - _PKT_OFFSET
    pkt_end = pkt_start + timedelta(days=1)

    stmt = (
        select(Appointment)
        .options(selectinload(Appointment.doctor), selectinload(Appointment.patient))
        .where(
            and_(
                Appointment.slot_start_utc >= pkt_start,
                Appointment.slot_start_utc < pkt_end,
                Appointment.status != "cancelled",
            )
        )
        .order_by(Appointment.slot_start_utc)
    )
    if doctor_id:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)
    appts = (await db.execute(stmt)).scalars().all()

    rows = []
    for a in appts:
        pkt_time = (a.slot_start_utc + _PKT_OFFSET).strftime("%H:%M")
        pat = a.patient
        doc = a.doctor
        rows.append(
            f"<tr><td>{pkt_time}</td>"
            f"<td>{(pat.name_en or pat.name_ur or '') if pat else ''}</td>"
            f"<td>{pat.phone_e164 if pat else ''}</td>"
            f"<td>{doc.name_en if doc else ''}</td>"
            f"<td>{a.appointment_type}</td>"
            f"<td>{a.status}</td></tr>"
        )

    html = (
        "<!DOCTYPE html><html><head><title>Day Sheet — "
        f"{target.isoformat()}</title>"
        "<style>body{font-family:sans-serif;padding:20px;}"
        "table{width:100%;border-collapse:collapse;}"
        "th,td{border:1px solid #ccc;padding:8px;text-align:left;}"
        "th{background:#f3f4f6;}h1{font-size:18px;}@media print{button{display:none;}}"
        "</style></head><body>"
        f"<h1>Day Sheet — {target.isoformat()} (PKT)</h1>"
        f"<p>Total: {len(appts)} appointment(s)</p>"
        "<button onclick='window.print()'>Print</button>"
        "<table><thead><tr><th>Time</th><th>Patient</th><th>Phone</th>"
        "<th>Doctor</th><th>Type</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></body></html>"
    )
    return HTMLResponse(content=html)


@router.get("/{appointment_id}", response_model=AppointmentResponse)
async def get_appointment(
    appointment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> AppointmentResponse:
    result = await db.execute(select(Appointment).where(Appointment.id == appointment_id))
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")
    return AppointmentResponse.model_validate(appointment)


@router.put("/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    appointment_id: int,
    request: Request,
    body: AppointmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> AppointmentResponse:
    result = await db.execute(select(Appointment).where(Appointment.id == appointment_id))
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(appointment, field, value)

    await _audit(
        db,
        action="PHI_UPDATE",
        entity_type="appointment",
        entity_id=appointment.id,
        user=current_user,
        request=request,
        notes=f"Appointment updated. Fields: {', '.join(update_data.keys())}",
    )

    await db.commit()
    await db.refresh(appointment)
    return AppointmentResponse.model_validate(appointment)


@router.delete("/{appointment_id}", status_code=status.HTTP_200_OK, response_model=AppointmentResponse)
async def cancel_appointment(
    appointment_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> AppointmentResponse:
    result = await db.execute(select(Appointment).where(Appointment.id == appointment_id))
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")

    if appointment.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Appointment is already cancelled.",
        )

    appointment.status = "cancelled"
    appointment.cancelled_at = datetime.now(timezone.utc)
    appointment.cancelled_by = "staff"

    await _audit(
        db,
        action="APPOINTMENT_CANCEL",
        entity_type="appointment",
        entity_id=appointment.id,
        user=current_user,
        request=request,
        notes="Appointment cancelled by staff via DELETE endpoint",
    )

    await db.commit()
    await db.refresh(appointment)
    return AppointmentResponse.model_validate(appointment)
