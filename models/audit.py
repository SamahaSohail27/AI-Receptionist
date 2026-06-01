from sqlalchemy import ForeignKey, String, DateTime, Text, Index
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base
from datetime import datetime
from typing import Optional


class AuditLog(Base):
    """
    Append-only audit trail for all PHI access and data mutations.
    Never update or delete rows — compliance requirement.
    """
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_user", "user_id"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))    # null for system actions
    user_email: Mapped[Optional[str]] = mapped_column(String(200))
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))  # IPv4 or IPv6
    session_id: Mapped[Optional[str]] = mapped_column(String(36))

    action: Mapped[str] = mapped_column(
        String(50), nullable=False
        # PHI_ACCESS | PHI_UPDATE | PHI_CREATE | LOGIN | LOGOUT
        # APPOINTMENT_BOOK | APPOINTMENT_CANCEL | PROVIDER_CONFIG_CHANGE
    )
    entity_type: Mapped[Optional[str]] = mapped_column(String(50))   # patient | appointment | ...
    entity_id: Mapped[Optional[int]] = mapped_column()

    old_value: Mapped[Optional[str]] = mapped_column(Text)  # JSON string, PHI redacted
    new_value: Mapped[Optional[str]] = mapped_column(Text)  # JSON string, PHI redacted
    notes: Mapped[Optional[str]] = mapped_column(String(500))


class NotificationLog(Base, ):
    """Track all outbound notifications (WhatsApp, SMS)."""
    __tablename__ = "notification_log"
    __table_args__ = (
        Index("ix_notif_appointment", "appointment_id"),
        Index("ix_notif_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), nullable=False)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)

    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # whatsapp | sms
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # twilio | plivo
    language: Mapped[str] = mapped_column(String(10), nullable=False)

    notification_type: Mapped[str] = mapped_column(
        String(30), nullable=False
        # confirmation | reminder_24h | reminder_2h | cancellation
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
        # pending | sent | delivered | failed
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(String(500))
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    from datetime import datetime as _dt
