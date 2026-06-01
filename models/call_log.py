from sqlalchemy import ForeignKey, String, Boolean, DateTime, Integer, Float, Text, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .patient import Patient


class CallLog(Base, TimestampMixin):
    """
    One record per call. PHI fields: patient_id, caller_phone_hash.
    All latency / cost / provider data is non-PHI and safe for analytics.
    """
    __tablename__ = "call_logs"
    __table_args__ = (
        Index("ix_call_logs_session", "session_id", unique=True),
        Index("ix_call_logs_patient", "patient_id"),
        Index("ix_call_logs_created", "created_at"),
        Index("ix_call_logs_outcome", "outcome"),
        Index("ix_call_logs_language", "language"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)   # UUID per call

    # PHI: patient link (use patient_id — never name+phone in same row as clinical data)
    patient_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patients.id"))
    caller_phone_hash: Mapped[Optional[str]] = mapped_column(String(64))  # SHA-256 of E.164

    language: Mapped[str] = mapped_column(String(10), nullable=False)   # ur-PK | pa-PK | en
    intent: Mapped[Optional[str]] = mapped_column(String(50))
    outcome: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unknown"
        # booked | cancelled | rescheduled | answered | transferred | emergency_transfer | abandoned | unknown
    )

    telephony_provider: Mapped[str] = mapped_column(String(20), nullable=False)   # plivo | twilio
    stt_provider: Mapped[str] = mapped_column(String(20), nullable=False)
    llm_provider: Mapped[str] = mapped_column(String(20), nullable=False)
    tts_provider: Mapped[str] = mapped_column(String(20), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(100), nullable=False)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Latency (ms) — averages over all turns
    avg_stt_ms: Mapped[Optional[int]] = mapped_column(Integer)
    avg_llm_ms: Mapped[Optional[int]] = mapped_column(Integer)
    avg_tts_ms: Mapped[Optional[int]] = mapped_column(Integer)
    avg_total_ms: Mapped[Optional[int]] = mapped_column(Integer)
    p95_total_ms: Mapped[Optional[int]] = mapped_column(Integer)
    tts_cache_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tts_cache_misses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Cost (USD, 6 decimal places)
    cost_stt_usd: Mapped[Optional[float]] = mapped_column(Float)
    cost_llm_usd: Mapped[Optional[float]] = mapped_column(Float)
    cost_tts_usd: Mapped[Optional[float]] = mapped_column(Float)
    cost_telephony_usd: Mapped[Optional[float]] = mapped_column(Float)
    cost_total_usd: Mapped[Optional[float]] = mapped_column(Float)

    escalation_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    escalation_reason: Mapped[Optional[str]] = mapped_column(String(50))
    emergency_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    appointment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("appointments.id"))

    # Per-turn latency breakdown stored as JSON array
    turn_metrics: Mapped[Optional[list]] = mapped_column(JSON)

    patient: Mapped[Optional["Patient"]] = relationship(back_populates="call_logs")


class Transcript(Base, TimestampMixin):
    """
    One record per turn (STT utterance).
    PHI fields: raw_text (may contain patient name/symptoms)
    masked_text: PII replaced with [PATIENT_NAME], [PHONE], etc.
    """
    __tablename__ = "transcripts"
    __table_args__ = (
        Index("ix_transcripts_call", "call_log_id"),
        Index("ix_transcripts_session", "session_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    call_log_id: Mapped[int] = mapped_column(ForeignKey("call_logs.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    turn_id: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(10), nullable=False)   # patient | assistant

    language: Mapped[str] = mapped_column(String(10), nullable=False)
    is_rtl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # PHI: raw STT output or LLM response
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Non-PHI: PII masked version for analytics/display
    masked_text: Mapped[Optional[str]] = mapped_column(Text)

    stt_confidence: Mapped[Optional[float]] = mapped_column(Float)
    stt_provider: Mapped[Optional[str]] = mapped_column(String(20))
