from sqlalchemy import String, Date, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .appointment import Appointment
    from .call_log import CallLog


class Patient(Base, TimestampMixin):
    """
    PHI fields: cnic_hash, phone_e164, name_ur, name_en, date_of_birth
    Never log patient_name + cnic together — use patient_id for cross-referencing.
    """
    __tablename__ = "patients"
    __table_args__ = (
        Index("ix_patients_cnic_hash", "cnic_hash", unique=True),
        Index("ix_patients_phone", "phone_e164"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # PHI: CNIC stored as SHA-256 hash for lookups — never plaintext
    cnic_hash: Mapped[Optional[str]] = mapped_column(String(64))  # SHA-256 hex

    # PHI: Phone in E.164 format
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)

    # PHI: Name fields
    name_en: Mapped[Optional[str]] = mapped_column(String(200))
    name_ur: Mapped[Optional[str]] = mapped_column(String(200))   # Urdu script

    # PHI: Date of birth (year only stored for age-gating, not full DOB)
    birth_year: Mapped[Optional[int]] = mapped_column()

    gender: Mapped[Optional[str]] = mapped_column(String(1))   # M | F | O

    preferred_language: Mapped[str] = mapped_column(String(10), default="ur-PK", nullable=False)

    whatsapp_opted_in: Mapped[bool] = mapped_column(default=False, nullable=False)
    whatsapp_consent_at: Mapped[Optional[str]] = mapped_column(String(30))  # ISO timestamp

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")
    call_logs: Mapped[list["CallLog"]] = relationship(back_populates="patient")
