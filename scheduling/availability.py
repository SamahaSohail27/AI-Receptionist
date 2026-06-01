from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

import pytz
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.appointment import Appointment
from models.doctor import Doctor, DoctorAvailability
from scheduling.pkt_calendar import PKT, PKTCalendar

if TYPE_CHECKING:
    pass

UTC = pytz.utc


@dataclass
class TimeSlot:
    """A candidate appointment window for a specific doctor."""

    doctor_id: int
    start_utc: datetime
    end_utc: datetime
    duration_minutes: int
    is_available: bool = field(default=True)

    @property
    def start_pkt(self) -> datetime:
        """Convenience accessor — slot start expressed in PKT."""
        return self.start_utc.astimezone(PKT)

    @property
    def end_pkt(self) -> datetime:
        """Convenience accessor — slot end expressed in PKT."""
        return self.end_utc.astimezone(PKT)


class AvailabilityEngine:
    """
    Generate bookable appointment slots for a doctor over a date range.

    All internal calculations use PKT (UTC+5, no DST).  UTC-aware datetimes
    are persisted to / read from the database.
    """

    def __init__(
        self,
        db: AsyncSession,
        calendar: PKTCalendar | None = None,
    ) -> None:
        self._db = db
        self._calendar = calendar or PKTCalendar()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_available_slots(
        self,
        doctor_id: int,
        from_date: date,
        to_date: date,
        holidays: list[dict] | None = None,
        business_hours: dict | None = None,
    ) -> list[TimeSlot]:
        """
        Return all bookable slots for *doctor_id* in [from_date, to_date].

        Parameters
        ----------
        doctor_id:
            PK of the Doctor record.
        from_date / to_date:
            Inclusive date range (PKT dates).
        holidays:
            List of ``{"date": "YYYY-MM-DD", "name": "..."}`` dicts.
            Falls back to ``DEFAULT_HOLIDAYS`` when *None*.
        business_hours:
            Dict keyed by ISO weekday (0=Mon … 6=Sun), each value
            ``{"start": "HH:MM", "end": "HH:MM", "is_open": bool}``.
            When *None* every day is treated as open (i.e. only
            DoctorAvailability records limit the schedule).

        Returns
        -------
        list[TimeSlot]
            Slots where ``is_available=True`` only.
        """
        from scheduling.pkt_calendar import DEFAULT_HOLIDAYS  # avoid circular at module level

        if holidays is None:
            holidays = DEFAULT_HOLIDAYS

        # 1. Fetch doctor — need consultation_duration_minutes
        doctor = await self._fetch_doctor(doctor_id)
        duration = doctor.consultation_duration_minutes

        # 2. Fetch all active DoctorAvailability records for this doctor,
        #    keyed by day_of_week for O(1) lookup.
        availability_map: dict[int, DoctorAvailability] = (
            await self._fetch_availability_map(doctor_id)
        )

        # 3. Pre-fetch confirmed appointments in the whole date range so we
        #    avoid N+1 DB round-trips inside the inner loop.
        booked_starts: set[datetime] = await self._fetch_booked_starts(
            doctor_id, from_date, to_date
        )

        slots: list[TimeSlot] = []

        # Iterate every calendar day in [from_date, to_date]
        current = from_date
        while current <= to_date:
            day_of_week = current.weekday()  # 0=Mon … 6=Sun

            # Skip holidays
            if PKTCalendar.is_holiday(current, holidays):
                current += timedelta(days=1)
                continue

            # Skip days with no doctor availability
            avail = availability_map.get(day_of_week)
            if avail is None or not avail.is_active:
                current += timedelta(days=1)
                continue

            # Optional business_hours guard — if configured the doctor's
            # window must fall inside business hours.
            if business_hours is not None:
                # Use the availability start time as the reference point.
                start_h, start_m = map(int, avail.start_time.split(":"))
                check_dt = PKT.localize(
                    datetime.combine(current, time(start_h, start_m))
                )
                if not PKTCalendar.is_business_hour(check_dt, business_hours):
                    current += timedelta(days=1)
                    continue

            # Generate slots for this day
            day_slots = self._generate_day_slots(
                doctor_id=doctor_id,
                d=current,
                avail=avail,
                duration=duration,
                booked_starts=booked_starts,
            )
            slots.extend(day_slots)

            current += timedelta(days=1)

        return slots

    async def get_next_available(
        self,
        doctor_id: int,
        preferred_date: date,
        max_days_ahead: int = 14,
        holidays: list[dict] | None = None,
        business_hours: dict | None = None,
    ) -> TimeSlot | None:
        """
        Return the first available slot on or after *preferred_date*.

        Searches up to *max_days_ahead* days from *preferred_date*.
        Returns *None* if nothing is found within that window.
        """
        to_date = preferred_date + timedelta(days=max_days_ahead)
        slots = await self.get_available_slots(
            doctor_id=doctor_id,
            from_date=preferred_date,
            to_date=to_date,
            holidays=holidays,
            business_hours=business_hours,
        )
        return slots[0] if slots else None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fetch_doctor(self, doctor_id: int) -> Doctor:
        result = await self._db.execute(
            select(Doctor).where(Doctor.id == doctor_id)
        )
        doctor = result.scalar_one_or_none()
        if doctor is None:
            raise ValueError(f"Doctor with id={doctor_id} not found")
        return doctor

    async def _fetch_availability_map(
        self, doctor_id: int
    ) -> dict[int, DoctorAvailability]:
        result = await self._db.execute(
            select(DoctorAvailability).where(
                and_(
                    DoctorAvailability.doctor_id == doctor_id,
                    DoctorAvailability.is_active.is_(True),
                )
            )
        )
        rows = result.scalars().all()
        return {row.day_of_week: row for row in rows}

    async def _fetch_booked_starts(
        self,
        doctor_id: int,
        from_date: date,
        to_date: date,
    ) -> set[datetime]:
        """
        Return UTC-aware datetimes of all non-cancelled appointment starts
        for *doctor_id* within the given date range.
        """
        range_start_utc = PKTCalendar.start_of_day_utc(from_date)
        range_end_utc = PKTCalendar.end_of_day_utc(to_date)

        result = await self._db.execute(
            select(Appointment.slot_start_utc).where(
                and_(
                    Appointment.doctor_id == doctor_id,
                    Appointment.slot_start_utc >= range_start_utc,
                    Appointment.slot_start_utc <= range_end_utc,
                    Appointment.status != "cancelled",
                )
            )
        )
        return {row[0] for row in result.all()}

    def _generate_day_slots(
        self,
        doctor_id: int,
        d: date,
        avail: DoctorAvailability,
        duration: int,
        booked_starts: set[datetime],
    ) -> list[TimeSlot]:
        """
        Generate all possible slots for *d* based on *avail*, marking each
        as available or booked.  Only available slots are returned.
        """
        start_h, start_m = map(int, avail.start_time.split(":"))
        end_h, end_m = map(int, avail.end_time.split(":"))

        window_start_pkt = PKT.localize(
            datetime.combine(d, time(start_h, start_m))
        )
        window_end_pkt = PKT.localize(
            datetime.combine(d, time(end_h, end_m))
        )
        slot_delta = timedelta(minutes=duration)

        slots: list[TimeSlot] = []
        cursor = window_start_pkt

        while cursor + slot_delta <= window_end_pkt:
            cursor_utc = cursor.astimezone(UTC)
            end_utc = (cursor + slot_delta).astimezone(UTC)

            is_booked = cursor_utc in booked_starts

            if not is_booked:
                slots.append(
                    TimeSlot(
                        doctor_id=doctor_id,
                        start_utc=cursor_utc,
                        end_utc=end_utc,
                        duration_minutes=duration,
                        is_available=True,
                    )
                )

            cursor += slot_delta

        return slots
