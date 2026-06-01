"""
Central config — all environment variables loaded here.
Accessed via the module-level `settings` singleton.
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # -----------------------------------------------------------------------
    # Application
    # -----------------------------------------------------------------------
    app_name: str = "AI Medical Receptionist"
    debug: bool = False
    secret_key: str = "change-me-in-production"
    api_prefix: str = "/api/v1"
    allowed_origins: list[str] = ["http://localhost:8000"]

    # -----------------------------------------------------------------------
    # Database
    # -----------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_receptionist"

    # -----------------------------------------------------------------------
    # Redis / Celery
    # -----------------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # -----------------------------------------------------------------------
    # JWT
    # -----------------------------------------------------------------------
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 hours (clinic shift)

    # -----------------------------------------------------------------------
    # Telephony
    # -----------------------------------------------------------------------
    plivo_auth_id: str = ""
    plivo_auth_token: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    telnyx_api_key: str = ""
    telephony_primary: str = "plivo"
    telephony_fallback: str = "twilio"

    # -----------------------------------------------------------------------
    # STT
    # -----------------------------------------------------------------------
    deepgram_api_key: str = ""
    groq_api_key: str = ""
    azure_speech_key: str = ""
    azure_speech_region: str = "eastus"
    google_credentials_json: str = ""
    assemblyai_api_key: str = ""

    # -----------------------------------------------------------------------
    # LLM
    # -----------------------------------------------------------------------
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    google_api_key: str = ""
    llm_provider: str = "openai"
    llm_model_standard: str = "gpt-4o-mini"
    llm_model_quality: str = "gpt-4o"
    llm_temperature: float = 0.3

    # -----------------------------------------------------------------------
    # TTS
    # -----------------------------------------------------------------------
    elevenlabs_api_key: str = ""
    cartesia_api_key: str = ""

    # -----------------------------------------------------------------------
    # Voice Pipeline (SACRED — do not change without full test run)
    # -----------------------------------------------------------------------
    vad_confidence: float = 0.6
    vad_start_secs: float = 0.2
    vad_stop_secs: float = 0.6
    vad_min_volume: float = 0.5
    stt_confidence_ur: float = 0.45
    stt_confidence_en: float = 0.70
    stt_confidence_pa: float = 0.40
    stt_endpointing_ms: int = 600
    stt_utterance_end_ms: int = 1500

    # -----------------------------------------------------------------------
    # Scheduling
    # -----------------------------------------------------------------------
    slot_lock_seconds: int = 45
    default_language: str = "ur-PK"
    timezone: str = "Asia/Karachi"
    reminder_hours_before: str = "24,2"

    # -----------------------------------------------------------------------
    # TTS Cache
    # -----------------------------------------------------------------------
    tts_cache_dir: str = "tts_cache"
    tts_cache_max_mb: int = 500

    # -----------------------------------------------------------------------
    # WebSocket
    # -----------------------------------------------------------------------
    ws_port: int = 8001

    @field_validator("database_url", mode="before")
    @classmethod
    def _database_url_from_env(cls, v: str) -> str:
        return os.environ.get("DATABASE_URL", v)

    def has_openai(self) -> bool:
        return len(self.openai_api_key) > 0

    def has_anthropic(self) -> bool:
        return len(self.anthropic_api_key) > 0

    def has_deepgram(self) -> bool:
        return len(self.deepgram_api_key) > 0

    def has_plivo(self) -> bool:
        return len(self.plivo_auth_id) > 0 and len(self.plivo_auth_token) > 0

    def has_azure_speech(self) -> bool:
        return len(self.azure_speech_key) > 0

    def has_groq(self) -> bool:
        return len(self.groq_api_key) > 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
