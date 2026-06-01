"""
Language config API — default language, per-doctor language overrides, DTMF settings.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.auth import require_admin, require_any_staff
from core.database import get_db
from core.ws_manager import ws_manager
from models.clinic import ClinicConfig, ProviderConfig
from models.doctor import Doctor
from providers.language_profile import LANGUAGE_PROFILES

router = APIRouter(prefix="/language", tags=["language"])


class LanguageConfigUpdate(BaseModel):
    default_language: str | None = None
    dtmf_enabled: bool | None = None


class DoctorLanguageOverride(BaseModel):
    doctor_id: int
    preferred_language: str


@router.get("/profiles")
async def list_language_profiles(
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """Return all supported language profiles and their DTMF keys."""
    return {
        code: {
            "code": profile.code,
            "dtmf_key": profile.dtmf_key,
            "stt_provider": profile.stt.provider,
            "stt_language_code": profile.stt.language_code,
            "stt_confidence_threshold": profile.stt.confidence_threshold,
            "tts_provider": profile.tts.provider,
            "tts_voice": profile.tts.voice,
            "is_rtl": profile.is_rtl,
            "tts_fallback_note": profile.tts_fallback_note,
        }
        for code, profile in LANGUAGE_PROFILES.items()
    }


@router.get("/config")
async def get_language_config(
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """Return current clinic language configuration."""
    clinic = await db.scalar(select(ClinicConfig).limit(1))
    if not clinic:
        raise HTTPException(status_code=404, detail="Clinic config not found")
    return {
        "default_language": clinic.default_language,
        "dtmf_enabled": clinic.dtmf_enabled,
        "supported_languages": list(LANGUAGE_PROFILES.keys()),
    }


@router.put("/config")
async def update_language_config(
    body: LanguageConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_admin)] = None,
) -> dict:
    """Update clinic-level language settings."""
    clinic = await db.scalar(select(ClinicConfig).limit(1))
    if not clinic:
        raise HTTPException(status_code=404, detail="Clinic config not found")

    if body.default_language is not None:
        if body.default_language not in LANGUAGE_PROFILES:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported language: {body.default_language}. Valid: {list(LANGUAGE_PROFILES.keys())}",
            )
        clinic.default_language = body.default_language
    if body.dtmf_enabled is not None:
        clinic.dtmf_enabled = body.dtmf_enabled

    await db.flush()
    import asyncio
    asyncio.create_task(
        ws_manager.broadcast_json({"type": "config.updated", "entity": "language_config"})
    )
    return {"status": "updated", "default_language": clinic.default_language, "dtmf_enabled": clinic.dtmf_enabled}


@router.get("/doctors/overrides")
async def list_doctor_language_overrides(
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_any_staff)] = None,
) -> dict:
    """Return per-doctor language settings."""
    doctors = (await db.execute(select(Doctor).where(Doctor.is_active.is_(True)))).scalars().all()
    return {
        "data": [
            {"doctor_id": d.id, "name_en": d.name_en, "preferred_language": d.preferred_language}
            for d in doctors
        ]
    }


@router.put("/doctors/overrides")
async def set_doctor_language_override(
    body: DoctorLanguageOverride,
    db: AsyncSession = Depends(get_db),
    _: Annotated[None, Depends(require_admin)] = None,
) -> dict:
    """Set per-doctor language override."""
    if body.preferred_language not in LANGUAGE_PROFILES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported language: {body.preferred_language}",
        )
    doctor = await db.get(Doctor, body.doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    doctor.preferred_language = body.preferred_language
    await db.flush()
    return {"status": "updated", "doctor_id": body.doctor_id, "preferred_language": body.preferred_language}
