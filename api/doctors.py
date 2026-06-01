from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import require_admin, require_admin_or_doctor, require_any_staff
from core.database import get_db
from models.audit import AuditLog
from models.auth import User
from models.doctor import Doctor, DoctorAvailability

router = APIRouter(prefix="/doctors", tags=["doctors"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class DoctorCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name_en: str
    name_ur: Optional[str] = None
    speciality: str
    speciality_ur: Optional[str] = None
    department: str
    room_number: Optional[str] = None
    consultation_fee: int = 500
    consultation_duration_minutes: int = 20
    preferred_language: str = "ur-PK"
    calendar_color: str = "#0EA5E9"
    insurance_panels: list[str] = Field(default_factory=list)


class DoctorUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name_en: Optional[str] = None
    name_ur: Optional[str] = None
    speciality: Optional[str] = None
    speciality_ur: Optional[str] = None
    department: Optional[str] = None
    room_number: Optional[str] = None
    consultation_fee: Optional[int] = None
    consultation_duration_minutes: Optional[int] = None
    preferred_language: Optional[str] = None
    calendar_color: Optional[str] = None
    insurance_panels: Optional[list[str]] = None


class DoctorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name_en: str
    name_ur: Optional[str]
    speciality: str
    speciality_ur: Optional[str]
    department: str
    room_number: Optional[str]
    consultation_fee: int
    consultation_duration_minutes: int
    preferred_language: str
    calendar_color: str
    insurance_panels: list[str]
    is_active: bool
    created_at: datetime


class AvailabilitySlot(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    day_of_week: int = Field(..., ge=0, le=6)
    start_time: str = Field(..., pattern=r'^\d{2}:\d{2}$')
    end_time: str = Field(..., pattern=r'^\d{2}:\d{2}$')
    is_active: bool = True


class AvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int
    day_of_week: int
    start_time: str
    end_time: str
    is_active: bool


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
# Doctor routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[DoctorResponse])
async def list_doctors(
    speciality: Optional[str] = Query(default=None),
    department: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None, alias="active"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> list[DoctorResponse]:
    stmt = select(Doctor)

    if is_active is not None:
        stmt = stmt.where(Doctor.is_active == is_active)
    if speciality:
        stmt = stmt.where(Doctor.speciality == speciality)
    if department:
        stmt = stmt.where(Doctor.department == department)

    result = await db.execute(stmt)
    doctors = result.scalars().all()
    return [DoctorResponse.model_validate(d) for d in doctors]


@router.post("", response_model=DoctorResponse, status_code=status.HTTP_201_CREATED)
async def create_doctor(
    request: Request,
    body: DoctorCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> DoctorResponse:
    doctor = Doctor(
        name_en=body.name_en,
        name_ur=body.name_ur,
        speciality=body.speciality,
        speciality_ur=body.speciality_ur,
        department=body.department,
        room_number=body.room_number,
        consultation_fee=body.consultation_fee,
        consultation_duration_minutes=body.consultation_duration_minutes,
        preferred_language=body.preferred_language,
        calendar_color=body.calendar_color,
        insurance_panels=body.insurance_panels,
    )
    db.add(doctor)
    await db.flush()

    await _audit(
        db,
        action="PHI_CREATE",
        entity_type="doctor",
        entity_id=doctor.id,
        user=current_user,
        request=request,
        notes="Doctor record created",
    )

    await db.commit()
    await db.refresh(doctor)
    return DoctorResponse.model_validate(doctor)


@router.get("/{doctor_id}", response_model=DoctorResponse)
async def get_doctor(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> DoctorResponse:
    result = await db.execute(select(Doctor).where(Doctor.id == doctor_id))
    doctor = result.scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found.")
    return DoctorResponse.model_validate(doctor)


@router.put("/{doctor_id}", response_model=DoctorResponse)
async def update_doctor(
    doctor_id: int,
    request: Request,
    body: DoctorUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> DoctorResponse:
    result = await db.execute(select(Doctor).where(Doctor.id == doctor_id))
    doctor = result.scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found.")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(doctor, field, value)

    await _audit(
        db,
        action="PHI_UPDATE",
        entity_type="doctor",
        entity_id=doctor.id,
        user=current_user,
        request=request,
        notes=f"Fields updated: {', '.join(update_data.keys())}",
    )

    await db.commit()
    await db.refresh(doctor)
    return DoctorResponse.model_validate(doctor)


@router.delete("/{doctor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_doctor(
    doctor_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> None:
    result = await db.execute(select(Doctor).where(Doctor.id == doctor_id))
    doctor = result.scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found.")

    doctor.is_active = False

    await _audit(
        db,
        action="PHI_UPDATE",
        entity_type="doctor",
        entity_id=doctor.id,
        user=current_user,
        request=request,
        notes="Doctor soft-deleted (is_active=False)",
    )

    await db.commit()


# ---------------------------------------------------------------------------
# Availability routes
# ---------------------------------------------------------------------------

@router.get("/{doctor_id}/availability", response_model=list[AvailabilityResponse])
async def list_availability(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> list[AvailabilityResponse]:
    # Verify doctor exists
    doc_result = await db.execute(select(Doctor).where(Doctor.id == doctor_id))
    if doc_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found.")

    result = await db.execute(
        select(DoctorAvailability).where(DoctorAvailability.doctor_id == doctor_id)
    )
    slots = result.scalars().all()
    return [AvailabilityResponse.model_validate(s) for s in slots]


@router.post(
    "/{doctor_id}/availability",
    response_model=AvailabilityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_availability(
    doctor_id: int,
    request: Request,
    body: AvailabilitySlot,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_or_doctor),
) -> AvailabilityResponse:
    doc_result = await db.execute(select(Doctor).where(Doctor.id == doctor_id))
    if doc_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found.")

    slot = DoctorAvailability(
        doctor_id=doctor_id,
        day_of_week=body.day_of_week,
        start_time=body.start_time,
        end_time=body.end_time,
        is_active=body.is_active,
    )
    db.add(slot)
    await db.flush()

    await _audit(
        db,
        action="PHI_CREATE",
        entity_type="doctor_availability",
        entity_id=slot.id,
        user=current_user,
        request=request,
        notes=f"Availability slot created for doctor_id={doctor_id}",
    )

    await db.commit()
    await db.refresh(slot)
    return AvailabilityResponse.model_validate(slot)


@router.put("/{doctor_id}/availability/{slot_id}", response_model=AvailabilityResponse)
async def update_availability(
    doctor_id: int,
    slot_id: int,
    request: Request,
    body: AvailabilitySlot,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_or_doctor),
) -> AvailabilityResponse:
    result = await db.execute(
        select(DoctorAvailability).where(
            DoctorAvailability.id == slot_id,
            DoctorAvailability.doctor_id == doctor_id,
        )
    )
    slot = result.scalar_one_or_none()
    if slot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Availability slot not found.",
        )

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(slot, field, value)

    await _audit(
        db,
        action="PHI_UPDATE",
        entity_type="doctor_availability",
        entity_id=slot.id,
        user=current_user,
        request=request,
        notes=f"Availability slot updated for doctor_id={doctor_id}",
    )

    await db.commit()
    await db.refresh(slot)
    return AvailabilityResponse.model_validate(slot)


@router.delete("/{doctor_id}/availability/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_availability(
    doctor_id: int,
    slot_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_or_doctor),
) -> None:
    result = await db.execute(
        select(DoctorAvailability).where(
            DoctorAvailability.id == slot_id,
            DoctorAvailability.doctor_id == doctor_id,
        )
    )
    slot = result.scalar_one_or_none()
    if slot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Availability slot not found.",
        )

    await _audit(
        db,
        action="PHI_UPDATE",
        entity_type="doctor_availability",
        entity_id=slot.id,
        user=current_user,
        request=request,
        notes=f"Availability slot deleted for doctor_id={doctor_id}",
    )

    await db.delete(slot)
    await db.commit()
