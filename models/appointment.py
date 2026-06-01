from sqlalchemy import ForeignKey, String, Boolean, DateTime, Integer, Text, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .patient import Patient
    from .doctor import Doctor


class Appointment(Base, TimestampMixin):
    """
    Core booking record.
    slot_start_pkt / slot_end_pkt: stored as UTC in DB, displayed as PKT in UI.
    PHI fields: patient_id (FK), notes
    """
    __tablename__ = "appointments"
    __table_args__ = (
        # Prevent double-booking: one appointment per doctor per start slot
        UniqueConstraint("doctor_id", "slot_start_utc", name="uq_appointment_doctor_slot"),
        Index("ix_appointments_patient", "patient_id"),
        Index("ix_appointments_doctor_date", "doctor_id", "slot_start_utc"),
        Index("ix_appointments_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), nullable=False)

    # Stored in UTC — always display as PKT (Asia/Karachi, UTC+5, no DST)
    slot_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    slot_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="scheduled"
        # scheduled | confirmed | cancelled | completed | no_show | rescheduled
    )

    appointment_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="consultation"
        # consultation | follow_up | procedure | emergency
    )

    booking_source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="ai"
        # ai | manual | walk_in
    )

    call_log_id: Mapped[Optional[int]] = mapped_column()   # Link to the call that booked this
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), unique=True)  # session_id+slot_id

    # PHI: free-text notes (access-controlled, never in analytics)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reminder_2h_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    whatsapp_confirmation_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[Optional[str]] = mapped_column(String(10))  # patient | staff | system

    patient: Mapped["Patient"] = relationship(back_populates="appointments")
    doctor: Mapped["Doctor"] = relationship(back_populates="appointments")


class SlotReservation(Base):
    """
    Temporary slot locks during active booking flow.
    TTL enforced by Celery cleanup job (every 60 seconds).
    Redis also maintains a parallel lock (TTL 45s) for faster contention detection.
    """
    __tablename__ = "slot_reservations"
    __table_args__ = (
        UniqueConstraint("doctor_id", "slot_start_utc", name="uq_reservation_doctor_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(nullable=False)
    slot_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
