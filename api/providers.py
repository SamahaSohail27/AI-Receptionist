from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import require_admin
from core.config import settings
from core.database import get_db
from core.runtime_config import invalidate_voice_settings
from core.ws_manager import ws_manager
from models.clinic import ProviderConfig

router = APIRouter(prefix="/providers", tags=["providers"])
settings_router = APIRouter(prefix="/settings/providers", tags=["settings"])


# ---------------------------------------------------------------------------
# Flat settings surface used by templates/settings_providers.html
# ---------------------------------------------------------------------------

from providers.tts.elevenlabs import (
    DEFAULT_FEMALE_VOICE_ID,
    DEFAULT_MALE_VOICE_ID,
    VOICE_PRESETS,
)


_DEFAULT_FLAT_SETTINGS: dict = {
    "stt_provider": "deepgram",
    "stt_fallback_provider": "groq_whisper",
    "llm_provider": "openai",
    "llm_fallback_provider": "groq",
    "tts_provider": "azure",
    "tts_fallback_provider": "openai_tts",
    "elevenlabs_voice_id": "",
    "elevenlabs_voice_id_female": DEFAULT_FEMALE_VOICE_ID,
    "elevenlabs_voice_id_male": DEFAULT_MALE_VOICE_ID,
    "elevenlabs_model_id": "eleven_flash_v2_5",
    "openai_realtime_voice_female": "shimmer",
    "openai_realtime_voice_male": "echo",
    "openai_model": "gpt-4o-mini",
    "groq_model": "llama-3.1-8b-instant",
    "deepgram_model": "nova-2",
}

_ALLOWED_FLAT_KEYS: set[str] = set(_DEFAULT_FLAT_SETTINGS.keys())


async def _get_or_create_active_config(db: AsyncSession) -> ProviderConfig:
    result = await db.execute(
        select(ProviderConfig).where(ProviderConfig.is_active.is_(True)).limit(1)
    )
    config = result.scalar_one_or_none()
    if config is not None:
        return config
    config = ProviderConfig(
        clinic_id=1,
        telephony_primary=settings.telephony_primary,
        telephony_fallback=settings.telephony_fallback,
        language_providers={"_ui": dict(_DEFAULT_FLAT_SETTINGS)},
        stt_confidence_ur=settings.stt_confidence_ur,
        stt_confidence_en=settings.stt_confidence_en,
        stt_confidence_pa=settings.stt_confidence_pa,
        llm_model_standard=settings.llm_model_standard,
        llm_model_quality=settings.llm_model_quality,
        llm_temperature=settings.llm_temperature,
        is_active=True,
    )
    db.add(config)
    await db.flush()
    return config


def _flat_view(config: ProviderConfig) -> dict:
    base = dict(_DEFAULT_FLAT_SETTINGS)
    stored = (config.language_providers or {}).get("_ui") or {}
    base.update({k: v for k, v in stored.items() if k in _ALLOWED_FLAT_KEYS})
    base["openai_model"] = config.llm_model_standard or base["openai_model"]
    return base


@settings_router.get("")
async def get_flat_provider_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user=Depends(require_admin),
) -> dict:
    config = await _get_or_create_active_config(db)
    await db.commit()
    return _flat_view(config)


@settings_router.put("")
async def update_flat_provider_settings(
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user=Depends(require_admin),
) -> dict:
    config = await _get_or_create_active_config(db)

    incoming = {k: v for k, v in body.items() if k in _ALLOWED_FLAT_KEYS}
    merged = dict((config.language_providers or {}))
    ui = dict(merged.get("_ui") or {})
    ui.update(incoming)
    merged["_ui"] = ui
    config.language_providers = merged

    if "openai_model" in incoming and isinstance(incoming["openai_model"], str):
        config.llm_model_standard = incoming["openai_model"]

    await db.commit()
    await db.refresh(config)

    # Tell every running session to re-read voice settings on its next turn.
    invalidate_voice_settings()
    asyncio.create_task(
        ws_manager.broadcast_json({"type": "config.updated", "entity": "provider_config"})
    )
    return _flat_view(config)


@settings_router.post("/test")
async def test_provider_connection(
    body: dict,
    current_user=Depends(require_admin),
) -> dict:
    """
    Lightweight connectivity probe used by the settings UI.
    Real synthesis/transcription tests are intentionally out of scope here —
    we just confirm the API key for the configured provider is present.
    """
    kind = (body.get("provider") or "").lower()
    t0 = time.monotonic()

    has_keys = {
        "stt": settings.has_deepgram() or settings.has_groq() or settings.has_azure_speech(),
        "llm": settings.has_openai() or settings.has_anthropic() or settings.has_groq(),
        "tts": (
            len(settings.elevenlabs_api_key) > 0
            or len(settings.azure_speech_key) > 0
            or len(settings.openai_api_key) > 0
            or len(settings.cartesia_api_key) > 0
        ),
    }
    if kind not in has_keys:
        return {"ok": False, "error": f"Unknown provider kind: {kind}. Use 'stt', 'llm', or 'tts'."}

    latency_ms = max(1, int((time.monotonic() - t0) * 1000))
    if not has_keys[kind]:
        return {"ok": False, "error": f"No {kind.upper()} provider API key configured in .env"}

    if kind == "llm":
        return {"ok": True, "ttft_ms": latency_ms}
    if kind == "tts":
        return {"ok": True, "bytes": 0, "latency_ms": latency_ms}
    return {"ok": True, "latency_ms": latency_ms}


@settings_router.get("/elevenlabs-voices")
async def list_elevenlabs_voices(
    current_user=Depends(require_admin),
) -> dict:
    """Curated, conversational ElevenLabs voices grouped by gender."""
    female = [v for v in VOICE_PRESETS if v["gender"] == "female"]
    male = [v for v in VOICE_PRESETS if v["gender"] == "male"]
    return {
        "female": female,
        "male": male,
        "default_female": DEFAULT_FEMALE_VOICE_ID,
        "default_male": DEFAULT_MALE_VOICE_ID,
    }


@settings_router.get("/elevenlabs-models")
async def list_elevenlabs_models(
    current_user=Depends(require_admin),
) -> dict:
    """ElevenLabs models we'll allow. v3 is blocked at the adapter (HTTP 403)."""
    return {
        "models": [
            {
                "model_id": "eleven_flash_v2_5",
                "name": "Flash v2.5",
                "description": "Fastest — lowest latency, recommended for live calls",
            },
            {
                "model_id": "eleven_multilingual_v2",
                "name": "Multilingual v2",
                "description": "Most natural prosody, slightly slower",
            },
            {
                "model_id": "eleven_turbo_v2_5",
                "name": "Turbo v2.5",
                "description": "Balanced quality and latency",
            },
        ],
        "default": "eleven_flash_v2_5",
        "blocked_models": ["eleven_v3"],
    }


@settings_router.get("/openai-realtime-voices")
async def list_openai_realtime_voices(
    current_user=Depends(require_admin),
) -> dict:
    """Voices supported by OpenAI Realtime API (used when tts_provider=='openai')."""
    female = [
        {"voice_id": "shimmer", "name": "Shimmer", "description": "Warm, conversational"},
        {"voice_id": "coral",   "name": "Coral",   "description": "Bright, friendly"},
        {"voice_id": "sage",    "name": "Sage",    "description": "Calm, measured"},
        {"voice_id": "alloy",   "name": "Alloy",   "description": "Neutral, balanced"},
        {"voice_id": "ballad",  "name": "Ballad",  "description": "Soft, narrative"},
    ]
    male = [
        {"voice_id": "echo",    "name": "Echo",    "description": "Warm, confident"},
        {"voice_id": "verse",   "name": "Verse",   "description": "Articulate, professional"},
        {"voice_id": "ash",     "name": "Ash",     "description": "Calm, deep"},
    ]
    return {
        "female": female,
        "male": male,
        "default_female": "shimmer",
        "default_male": "echo",
    }


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ProviderConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clinic_id: int
    telephony_primary: str
    telephony_fallback: str
    language_providers: dict
    stt_confidence_ur: float
    stt_confidence_en: float
    stt_confidence_pa: float
    llm_model_standard: str
    llm_model_quality: str
    llm_temperature: float
    is_active: bool


class ProviderConfigUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    telephony_primary: Optional[str] = None
    telephony_fallback: Optional[str] = None
    language_providers: Optional[dict] = None
    stt_confidence_ur: Optional[float] = None
    stt_confidence_en: Optional[float] = None
    stt_confidence_pa: Optional[float] = None
    llm_model_standard: Optional[str] = None
    llm_model_quality: Optional[str] = None
    llm_temperature: Optional[float] = None
    is_active: Optional[bool] = None


class TTSTestRequest(BaseModel):
    provider: str
    language: str
    text: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/config", response_model=ProviderConfigResponse)
async def get_provider_config(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user=Depends(require_admin),
) -> ProviderConfigResponse:
    result = await db.execute(
        select(ProviderConfig).where(ProviderConfig.is_active.is_(True)).limit(1)
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active provider configuration found.",
        )
    return ProviderConfigResponse.model_validate(config)


@router.put("/config", response_model=ProviderConfigResponse)
async def update_provider_config(
    body: ProviderConfigUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user=Depends(require_admin),
) -> ProviderConfigResponse:
    result = await db.execute(
        select(ProviderConfig).where(ProviderConfig.is_active.is_(True)).limit(1)
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active provider configuration found.",
        )

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(config, field, value)

    await db.commit()
    await db.refresh(config)

    event = {"type": "config.updated", "entity": "provider_config"}
    asyncio.create_task(ws_manager.broadcast_json(event))

    return ProviderConfigResponse.model_validate(config)


@router.get("/health")
async def provider_health(
    current_user=Depends(require_admin),
) -> dict:
    return {
        "deepgram": len(settings.deepgram_api_key) > 0,
        "openai": len(settings.openai_api_key) > 0,
        "anthropic": len(settings.anthropic_api_key) > 0,
        "azure_speech": len(settings.azure_speech_key) > 0,
        "groq": len(settings.groq_api_key) > 0,
        "plivo": len(settings.plivo_auth_id) > 0 and len(settings.plivo_auth_token) > 0,
        "twilio": len(settings.twilio_account_sid) > 0 and len(settings.twilio_auth_token) > 0,
        "elevenlabs": len(settings.elevenlabs_api_key) > 0,
    }


@router.post("/test/tts")
async def test_tts(
    body: TTSTestRequest,
    current_user=Depends(require_admin),
) -> dict:
    provider = body.provider.lower()
    t0 = time.monotonic()
    success = False

    try:
        if provider == "elevenlabs":
            if not len(settings.elevenlabs_api_key) > 0:
                raise HTTPException(
                    status_code=status.HTTP_424_FAILED_DEPENDENCY,
                    detail="ElevenLabs API key not configured.",
                )
            from providers.tts.elevenlabs import ElevenLabsTTSAdapter
            adapter = ElevenLabsTTSAdapter(language=body.language)
            result = await adapter.synthesize(body.text)
            success = len(result.audio_bytes) > 0

        elif provider == "azure":
            if not len(settings.azure_speech_key) > 0:
                raise HTTPException(
                    status_code=status.HTTP_424_FAILED_DEPENDENCY,
                    detail="Azure Speech key not configured.",
                )
            from providers.tts.azure_neural import AzureNeuralTTSAdapter
            adapter = AzureNeuralTTSAdapter(language=body.language)
            result = await adapter.synthesize(body.text)
            success = len(result.audio_bytes) > 0

        elif provider == "openai":
            if not len(settings.openai_api_key) > 0:
                raise HTTPException(
                    status_code=status.HTTP_424_FAILED_DEPENDENCY,
                    detail="OpenAI API key not configured.",
                )
            from providers.tts.openai_tts import OpenAITTSAdapter
            adapter = OpenAITTSAdapter(language=body.language)
            result = await adapter.synthesize(body.text)
            success = len(result.audio_bytes) > 0

        elif provider == "cartesia":
            if not len(settings.cartesia_api_key) > 0:
                raise HTTPException(
                    status_code=status.HTTP_424_FAILED_DEPENDENCY,
                    detail="Cartesia API key not configured.",
                )
            from providers.tts.cartesia import CartesiaTTSAdapter
            adapter = CartesiaTTSAdapter(language=body.language)
            result = await adapter.synthesize(body.text)
            success = len(result.audio_bytes) > 0

        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown TTS provider: {body.provider}. Supported: elevenlabs, azure, openai, cartesia.",
            )

    except HTTPException:
        raise
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "provider": body.provider,
            "language": body.language,
            "latency_ms": latency_ms,
            "success": False,
            "error": str(exc),
        }

    latency_ms = int((time.monotonic() - t0) * 1000)
    return {
        "provider": body.provider,
        "language": body.language,
        "latency_ms": latency_ms,
        "success": success,
    }
