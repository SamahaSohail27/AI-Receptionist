from sqlalchemy import ForeignKey, String, Boolean, JSON, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .appointment import Appointment, DoctorAvailability


class Doctor(Base, TimestampMixin):
    __tablename__ = "doctors"
    __table_args__ = (
        Index("ix_doctors_speciality", "speciality"),
        Index("ix_doctors_gender", "gender"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name_en: Mapped[str] = mapped_column(String(200), nullable=False)
    name_ur: Mapped[Optional[str]] = mapped_column(String(200))    # Urdu script for TTS
    speciality: Mapped[str] = mapped_column(String(100), nullable=False)
    speciality_ur: Mapped[Optional[str]] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    room_number: Mapped[Optional[str]] = mapped_column(String(20))
    consultation_fee: Mapped[int] = mapped_column(Integer, nullable=False, default=500)  # PKR
    consultation_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(10), default="ur-PK", nullable=False)
    gender: Mapped[Optional[str]] = mapped_column(String(1))  # M | F | None — used by search_doctors gender filter

    # Color for UI calendar (hex)
    calendar_color: Mapped[str] = mapped_column(String(7), default="#0EA5E9", nullable=False)

    # Sehat Sahulat / insurance panel memberships: ["sehat_sahulat", "igloo", "jubilee"]
    insurance_panels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    availability: Mapped[list["DoctorAvailability"]] = relationship(back_populates="doctor")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="doctor")


class DoctorAvailability(Base, TimestampMixin):
    """
    Recurring weekly availability windows.
    day_of_week: 0=Monday, 1=Tuesday, ..., 6=Sunday (ISO weekday - 1)
    """
    __tablename__ = "doctor_availability"
    __table_args__ = (
        Index("ix_availability_doctor_day", "doctor_id", "day_of_week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), nullable=False)
    day_of_week: Mapped[int] = mapped_column(nullable=False)     # 0=Monday...6=Sunday
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)   # "09:00"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)     # "17:00"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    doctor: Mapped["Doctor"] = relationship(back_populates="availability")
