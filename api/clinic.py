from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import require_admin, require_any_staff
from core.database import get_db
from models.audit import AuditLog
from models.auth import User
from models.clinic import ClinicConfig

router = APIRouter(prefix="/clinic", tags=["clinic"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class BusinessHourEntry(BaseModel):
    start: str  # "HH:MM"
    end: str    # "HH:MM"
    is_open: bool


class ClinicConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_name: str
    clinic_name_ur: Optional[str]
    timezone: str
    default_language: str
    dtmf_enabled: bool
    whatsapp_reminders_enabled: bool
    reminder_hours_before: str
    slot_lock_seconds: int
    business_hours: dict
    holidays: list
    triage_nurse_number: Optional[str]
    after_hours_number: Optional[str]
    logo_url: Optional[str]
    address: Optional[str]
    phone_display: Optional[str]


class ClinicConfigUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    clinic_name: Optional[str] = None
    clinic_name_ur: Optional[str] = None
    timezone: Optional[str] = None
    default_language: Optional[str] = None
    dtmf_enabled: Optional[bool] = None
    whatsapp_reminders_enabled: Optional[bool] = None
    reminder_hours_before: Optional[str] = None
    slot_lock_seconds: Optional[int] = None
    business_hours: Optional[dict] = None
    holidays: Optional[list] = None
    triage_nurse_number: Optional[str] = None
    after_hours_number: Optional[str] = None
    logo_url: Optional[str] = None
    address: Optional[str] = None
    phone_display: Optional[str] = None


class HolidayCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    date: str  # "YYYY-MM-DD"
    name: str
    name_ur: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_clinic(db: AsyncSession) -> ClinicConfig:
    result = await db.execute(select(ClinicConfig).limit(1))
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clinic configuration not found.",
        )
    return config


async def _audit(
    db: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: Optional[int],
    user: User,
    request: Request,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
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
        old_value=old_value,
        new_value=new_value,
        notes=notes,
    )
    db.add(log)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/settings", response_model=ClinicConfigResponse)
async def get_clinic_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: User = Depends(require_any_staff),
) -> ClinicConfigResponse:
    config = await _get_clinic(db)
    return ClinicConfigResponse.model_validate(config)


@router.put("/settings", response_model=ClinicConfigResponse)
async def update_clinic_settings(
    request: Request,
    body: ClinicConfigUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: User = Depends(require_admin),
) -> ClinicConfigResponse:
    config = await _get_clinic(db)

    update_data = body.model_dump(exclude_unset=True)
    old_snapshot = {k: getattr(config, k) for k in update_data}

    for field, value in update_data.items():
        setattr(config, field, value)

    await _audit(
        db,
        action="PROVIDER_CONFIG_CHANGE",
        entity_type="clinic_config",
        entity_id=config.id,
        user=current_user,
        request=request,
        old_value=json.dumps(old_snapshot, default=str),
        new_value=json.dumps(update_data, default=str),
        notes=f"Clinic settings updated: {', '.join(update_data.keys())}",
    )

    await db.commit()
    await db.refresh(config)
    return ClinicConfigResponse.model_validate(config)


@router.get("/holidays")
async def get_holidays(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: User = Depends(require_any_staff),
) -> list:
    config = await _get_clinic(db)
    return config.holidays or []


@router.post("/holidays", status_code=status.HTTP_201_CREATED)
async def add_holiday(
    request: Request,
    body: HolidayCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: User = Depends(require_admin),
) -> dict:
    config = await _get_clinic(db)

    holidays: list = list(config.holidays or [])

    for existing in holidays:
        if existing.get("date") == body.date:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A holiday already exists for date {body.date}.",
            )

    new_holiday: dict = {"date": body.date, "name": body.name}
    if body.name_ur is not None:
        new_holiday["name_ur"] = body.name_ur
    holidays.append(new_holiday)

    config.holidays = holidays

    await _audit(
        db,
        action="PROVIDER_CONFIG_CHANGE",
        entity_type="clinic_config",
        entity_id=config.id,
        user=current_user,
        request=request,
        new_value=json.dumps(new_holiday),
        notes=f"Holiday added: {body.date} — {body.name}",
    )

    await db.commit()
    return new_holiday


@router.delete("/holidays/{date}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holiday(
    date: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: User = Depends(require_admin),
) -> None:
    config = await _get_clinic(db)

    holidays: list = list(config.holidays or [])
    updated = [h for h in holidays if h.get("date") != date]

    if len(updated) == len(holidays):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No holiday found for date {date}.",
        )

    config.holidays = updated

    await _audit(
        db,
        action="PROVIDER_CONFIG_CHANGE",
        entity_type="clinic_config",
        entity_id=config.id,
        user=current_user,
        request=request,
        old_value=json.dumps({"date": date}),
        notes=f"Holiday removed: {date}",
    )

    await db.commit()
