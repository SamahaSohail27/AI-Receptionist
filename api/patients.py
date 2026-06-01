from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import require_any_staff
from core.database import get_db
from models.audit import AuditLog
from models.auth import User
from models.patient import Patient

router = APIRouter(prefix="/patients", tags=["patients"])


# ---------------------------------------------------------------------------
# Phone normalisation
# ---------------------------------------------------------------------------

def normalize_phone(phone: str) -> str:
    digits = re.sub(r'\D', '', phone)
    if len(digits) == 11 and digits.startswith('0'):
        return '+92' + digits[1:]
    if len(digits) == 10:
        return '+92' + digits
    if digits.startswith('92') and len(digits) == 12:
        return '+' + digits
    return phone  # pass through if already normalised


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class PatientCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    phone_e164: str
    name_en: Optional[str] = None
    name_ur: Optional[str] = None
    birth_year: Optional[int] = None
    gender: Optional[str] = None
    preferred_language: str = "ur-PK"
    whatsapp_opted_in: bool = False

    @field_validator("gender")
    @classmethod
    def _validate_gender(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 1:
            raise ValueError("gender must be at most 1 character (M, F, or O)")
        return v


class PatientUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    phone_e164: Optional[str] = None
    name_en: Optional[str] = None
    name_ur: Optional[str] = None
    birth_year: Optional[int] = None
    gender: Optional[str] = None
    preferred_language: Optional[str] = None
    whatsapp_opted_in: Optional[bool] = None

    @field_validator("gender")
    @classmethod
    def _validate_gender(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 1:
            raise ValueError("gender must be at most 1 character (M, F, or O)")
        return v


class PatientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone_e164: str
    name_en: Optional[str]
    name_ur: Optional[str]
    birth_year: Optional[int]
    gender: Optional[str]
    preferred_language: str
    whatsapp_opted_in: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Helper: create an AuditLog record (async ORM, no executor needed)
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
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[PatientResponse])
async def list_patients(
    request: Request,
    q: Optional[str] = Query(default=None, description="Search name_en / name_ur / phone_e164"),
    language: Optional[str] = Query(default=None, description="Filter by preferred_language"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> list[PatientResponse]:
    stmt = select(Patient)

    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Patient.name_en.ilike(like),
                Patient.name_ur.ilike(like),
                Patient.phone_e164.ilike(like),
            )
        )

    if language:
        stmt = stmt.where(Patient.preferred_language == language)

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(stmt)
    patients = result.scalars().all()
    return [PatientResponse.model_validate(p) for p in patients]


@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
async def create_patient(
    request: Request,
    body: PatientCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> PatientResponse:
    normalised_phone = normalize_phone(body.phone_e164)

    # Uniqueness check
    existing = await db.execute(
        select(Patient).where(Patient.phone_e164 == normalised_phone)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A patient with this phone number already exists.",
        )

    patient = Patient(
        phone_e164=normalised_phone,
        name_en=body.name_en,
        name_ur=body.name_ur,
        birth_year=body.birth_year,
        gender=body.gender,
        preferred_language=body.preferred_language,
        whatsapp_opted_in=body.whatsapp_opted_in,
    )
    db.add(patient)
    await db.flush()  # get patient.id before audit log

    await _audit(
        db,
        action="PHI_CREATE",
        entity_type="patient",
        entity_id=patient.id,
        user=current_user,
        request=request,
        notes="Patient record created",
    )

    await db.commit()
    await db.refresh(patient)
    return PatientResponse.model_validate(patient)


@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    patient_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> PatientResponse:
    result = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

    await _audit(
        db,
        action="PHI_ACCESS",
        entity_type="patient",
        entity_id=patient.id,
        user=current_user,
        request=request,
        notes="Patient record accessed",
    )
    await db.commit()

    return PatientResponse.model_validate(patient)


@router.put("/{patient_id}", response_model=PatientResponse)
async def update_patient(
    patient_id: int,
    request: Request,
    body: PatientUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_any_staff),
) -> PatientResponse:
    result = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

    update_data = body.model_dump(exclude_unset=True)

    if "phone_e164" in update_data:
        normalised_phone = normalize_phone(update_data["phone_e164"])
        # Check uniqueness against other patients
        conflict = await db.execute(
            select(Patient).where(
                Patient.phone_e164 == normalised_phone,
                Patient.id != patient_id,
            )
        )
        if conflict.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another patient already uses this phone number.",
            )
        update_data["phone_e164"] = normalised_phone

    for field, value in update_data.items():
        setattr(patient, field, value)

    await _audit(
        db,
        action="PHI_UPDATE",
        entity_type="patient",
        entity_id=patient.id,
        user=current_user,
        request=request,
        notes=f"Fields updated: {', '.join(update_data.keys())}",
    )

    await db.commit()
    await db.refresh(patient)
    return PatientResponse.model_validate(patient)


@router.delete("/{patient_id}", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
async def delete_patient(
    patient_id: int,
    current_user: User = Depends(require_any_staff),
) -> None:
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Patient records cannot be deleted per data retention policy",
    )
