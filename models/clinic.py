from sqlalchemy import String, Boolean, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin
from typing import Optional


class ClinicConfig(Base, TimestampMixin):
    __tablename__ = "clinic_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_name: Mapped[str] = mapped_column(String(200), nullable=False)
    clinic_name_ur: Mapped[Optional[str]] = mapped_column(String(200))     # Urdu name for TTS
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Karachi", nullable=False)
    default_language: Mapped[str] = mapped_column(String(10), default="ur-PK", nullable=False)
    dtmf_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    whatsapp_reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sms_fallback_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reminder_hours_before: Mapped[str] = mapped_column(String(50), default="24,2", nullable=False)  # CSV
    slot_lock_seconds: Mapped[int] = mapped_column(default=45, nullable=False)
    max_concurrent_calls: Mapped[int] = mapped_column(default=20, nullable=False)

    # Business hours: {"monday": {"open": "09:00", "close": "17:00", "closed": false}, ...}
    business_hours: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Public holiday list: [{"name": "Eid ul-Fitr", "date": "2026-03-31", "days": 3}, ...]
    holidays: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    logo_url: Mapped[Optional[str]] = mapped_column(String(500))
    address: Mapped[Optional[str]] = mapped_column(Text)
    phone_display: Mapped[Optional[str]] = mapped_column(String(30))   # Human-readable display number

    triage_nurse_number: Mapped[Optional[str]] = mapped_column(String(20))  # E.164, for emergency transfer
    after_hours_number: Mapped[Optional[str]] = mapped_column(String(20))   # E.164


class ProviderConfig(Base, TimestampMixin):
    __tablename__ = "provider_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_id: Mapped[int] = mapped_column(nullable=False)

    # Telephony
    telephony_primary: Mapped[str] = mapped_column(String(20), default="plivo", nullable=False)
    telephony_fallback: Mapped[str] = mapped_column(String(20), default="twilio", nullable=False)

    # Per-language provider config stored as JSON
    # {"ur-PK": {"stt": "deepgram", "tts": "azure", "llm": "openai"}, ...}
    language_providers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # STT confidence thresholds (can be overridden per clinic from defaults)
    stt_confidence_ur: Mapped[float] = mapped_column(default=0.45, nullable=False)
    stt_confidence_en: Mapped[float] = mapped_column(default=0.70, nullable=False)
    stt_confidence_pa: Mapped[float] = mapped_column(default=0.40, nullable=False)

    # LLM settings
    llm_model_standard: Mapped[str] = mapped_column(String(100), default="gpt-4o-mini", nullable=False)
    llm_model_quality: Mapped[str] = mapped_column(String(100), default="gpt-4o", nullable=False)
    llm_temperature: Mapped[float] = mapped_column(default=0.3, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
