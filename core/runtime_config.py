"""
Runtime voice/TTS configuration cache.

Settings are persisted in `ProviderConfig.language_providers["_ui"]` (see
api/providers.py). Sessions read them through `load_voice_settings()` instead
of going to the DB on every turn. The PUT /settings/providers endpoint calls
`invalidate()` so the next session start picks up the new values.

The cache is process-local; if the app is scaled to multiple workers each
worker invalidates independently on its own writes. For multi-worker setups,
fan invalidation through `ws_manager` or Redis pub/sub.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AsyncSessionLocal
from models.clinic import ProviderConfig
from providers.tts.elevenlabs import (
    DEFAULT_FEMALE_VOICE_ID,
    DEFAULT_MALE_VOICE_ID,
    VOICE_PRESETS,
)

logger = logging.getLogger(__name__)


# OpenAI Realtime API supported voices.
_OPENAI_REALTIME_VOICES: set[str] = {
    "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse",
}
_DEFAULT_OPENAI_FEMALE = "shimmer"
_DEFAULT_OPENAI_MALE = "echo"

# Allowed ElevenLabs models (CLAUDE.md: v3 forbidden — HTTP 403).
ALLOWED_ELEVEN_MODELS: tuple[str, ...] = (
    "eleven_flash_v2_5",
    "eleven_multilingual_v2",
    "eleven_turbo_v2_5",
)
DEFAULT_ELEVEN_MODEL = "eleven_flash_v2_5"


@dataclass(frozen=True)
class VoiceSettings:
    """Resolved voice/TTS settings, ready to use by a session."""
    tts_provider: str = "openai"
    elevenlabs_female_voice_id: str = DEFAULT_FEMALE_VOICE_ID
    elevenlabs_male_voice_id: str = DEFAULT_MALE_VOICE_ID
    elevenlabs_custom_voice_id: str = ""
    elevenlabs_model_id: str = DEFAULT_ELEVEN_MODEL
    openai_realtime_female_voice: str = _DEFAULT_OPENAI_FEMALE
    openai_realtime_male_voice: str = _DEFAULT_OPENAI_MALE
    raw: dict = field(default_factory=dict)

    def voice_for_gender(self, gender: str = "female") -> str:
        """Return the right voice id for the active TTS provider + gender."""
        gender = (gender or "female").lower()
        if self.tts_provider == "elevenlabs":
            if self.elevenlabs_custom_voice_id:
                return self.elevenlabs_custom_voice_id
            return self.elevenlabs_male_voice_id if gender == "male" else self.elevenlabs_female_voice_id
        # OpenAI Realtime / OpenAI TTS — use OpenAI voice names.
        return self.openai_realtime_male_voice if gender == "male" else self.openai_realtime_female_voice


class _VoiceSettingsCache:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cached: Optional[VoiceSettings] = None

    def invalidate(self) -> None:
        """Drop cached settings; next load() will re-read from DB."""
        self._cached = None
        logger.info("runtime_config: voice settings cache invalidated")

    async def load(self, db: Optional[AsyncSession] = None) -> VoiceSettings:
        """Return cached settings or load fresh from DB. Thread-safe."""
        if self._cached is not None:
            return self._cached
        async with self._lock:
            if self._cached is not None:
                return self._cached
            if db is None:
                async with AsyncSessionLocal() as session:
                    self._cached = await self._read(session)
            else:
                self._cached = await self._read(db)
            return self._cached

    async def _read(self, db: AsyncSession) -> VoiceSettings:
        result = await db.execute(
            select(ProviderConfig).where(ProviderConfig.is_active.is_(True)).limit(1)
        )
        config = result.scalar_one_or_none()
        if config is None:
            return VoiceSettings()

        ui = (config.language_providers or {}).get("_ui") or {}
        return VoiceSettings(
            tts_provider=_normalize_provider(ui.get("tts_provider", "openai")),
            elevenlabs_female_voice_id=_pick_voice(
                ui.get("elevenlabs_voice_id_female"), DEFAULT_FEMALE_VOICE_ID, gender="female",
            ),
            elevenlabs_male_voice_id=_pick_voice(
                ui.get("elevenlabs_voice_id_male"), DEFAULT_MALE_VOICE_ID, gender="male",
            ),
            elevenlabs_custom_voice_id=(ui.get("elevenlabs_voice_id") or "").strip(),
            elevenlabs_model_id=_pick_model(ui.get("elevenlabs_model_id")),
            openai_realtime_female_voice=_pick_openai(
                ui.get("openai_realtime_voice_female"), _DEFAULT_OPENAI_FEMALE,
            ),
            openai_realtime_male_voice=_pick_openai(
                ui.get("openai_realtime_voice_male"), _DEFAULT_OPENAI_MALE,
            ),
            raw=dict(ui),
        )


def _normalize_provider(value: str) -> str:
    v = (value or "").lower().strip()
    # The settings UI uses "azure" / "openai_tts" / "elevenlabs" / etc. We
    # collapse the OpenAI variants — the realtime proxy treats them all as
    # "OpenAI native voice" and the pipecat pipeline already maps to OpenAITTS.
    if v in {"openai_tts", "openai"}:
        return "openai"
    if v == "elevenlabs":
        return "elevenlabs"
    if v == "azure":
        return "azure"
    if v in {"google_tts", "google"}:
        return "google"
    if v == "cartesia":
        return "cartesia"
    return "openai"


def _pick_voice(value: Optional[str], default: str, gender: str) -> str:
    if value and any(p["voice_id"] == value for p in VOICE_PRESETS):
        return value
    # Fall back to the first preset of the requested gender.
    for p in VOICE_PRESETS:
        if p["gender"] == gender:
            return p["voice_id"]
    return default


def _pick_model(value: Optional[str]) -> str:
    if value in ALLOWED_ELEVEN_MODELS:
        return value
    if value and value.startswith("eleven_v3"):
        logger.warning("runtime_config: ElevenLabs v3 (%s) requested but blocked — using %s", value, DEFAULT_ELEVEN_MODEL)
    return DEFAULT_ELEVEN_MODEL


def _pick_openai(value: Optional[str], default: str) -> str:
    if value and value.lower() in _OPENAI_REALTIME_VOICES:
        return value.lower()
    return default


# Module-level singleton.
_cache = _VoiceSettingsCache()


async def load_voice_settings(db: Optional[AsyncSession] = None) -> VoiceSettings:
    """Public entry point — sessions call this on start."""
    return await _cache.load(db)


def invalidate_voice_settings() -> None:
    """Call this from PUT /settings/providers after commit."""
    _cache.invalidate()
