from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytz

# ---------------------------------------------------------------------------
# Pakistan Standard Time — UTC+5, NO daylight saving time
# ---------------------------------------------------------------------------
PKT = pytz.timezone("Asia/Karachi")
UTC = pytz.utc

# ---------------------------------------------------------------------------
# Static Pakistani public holidays.
# Eid dates are NOT included here — they are variable (lunar calendar) and
# must be entered by clinic admin via ClinicConfig.holidays JSON.
# ---------------------------------------------------------------------------
DEFAULT_HOLIDAYS: list[dict] = [
    {"date": "2024-03-23", "name": "Pakistan Day"},
    {"date": "2024-05-01", "name": "Labour Day"},
    {"date": "2024-08-14", "name": "Independence Day"},
    {"date": "2024-11-09", "name": "Allama Iqbal Day"},
    {"date": "2024-12-25", "name": "Quaid-e-Azam Day"},
    {"date": "2025-03-23", "name": "Pakistan Day"},
    {"date": "2025-05-01", "name": "Labour Day"},
    {"date": "2025-08-14", "name": "Independence Day"},
    {"date": "2025-11-09", "name": "Allama Iqbal Day"},
    {"date": "2025-12-25", "name": "Quaid-e-Azam Day"},
]


class PKTCalendar:
    """
    All datetime helpers for Pakistan Standard Time (UTC+5, no DST).

    Never use timedelta(hours=5) as a timezone object.  Always use the
    PKT constant (pytz.timezone("Asia/Karachi")) so that pytz can resolve
    the correct UTC offset from its internal IANA database.
    """

    PKT: pytz.BaseTzInfo = PKT

    # ------------------------------------------------------------------
    # Current time helpers
    # ------------------------------------------------------------------

    @staticmethod
    def now_pkt() -> datetime:
        """Return the current moment expressed in PKT (UTC+5, no DST)."""
        return datetime.now(tz=UTC).astimezone(PKT)

    @staticmethod
    def today_pkt() -> date:
        """Return today's date according to PKT."""
        return PKTCalendar.now_pkt().date()

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def to_pkt(dt: datetime) -> datetime:
        """
        Convert any timezone-aware datetime to PKT.

        If *dt* is naive it is assumed to be UTC, localised, then converted.
        """
        if dt.tzinfo is None:
            dt = UTC.localize(dt)
        return dt.astimezone(PKT)

    @staticmethod
    def to_utc(dt: datetime) -> datetime:
        """
        Convert a PKT datetime (or any aware datetime) to UTC.

        If *dt* is naive it is assumed to already be PKT, localised first.
        """
        if dt.tzinfo is None:
            dt = PKT.localize(dt)
        return dt.astimezone(UTC)

    # ------------------------------------------------------------------
    # Day boundary helpers — all returned as UTC-aware datetimes
    # ------------------------------------------------------------------

    @staticmethod
    def start_of_day_utc(d: date) -> datetime:
        """Return PKT midnight for *d* expressed as a UTC-aware datetime."""
        pkt_midnight = PKT.localize(datetime.combine(d, time(0, 0, 0)))
        return pkt_midnight.astimezone(UTC)

    @staticmethod
    def end_of_day_utc(d: date) -> datetime:
        """Return PKT 23:59:59.999999 for *d* expressed as a UTC-aware datetime."""
        pkt_end = PKT.localize(
            datetime.combine(d, time(23, 59, 59, 999999))
        )
        return pkt_end.astimezone(UTC)

    # ------------------------------------------------------------------
    # Business hours
    # ------------------------------------------------------------------

    @staticmethod
    def is_business_hour(dt: datetime, business_hours: dict) -> bool:
        """
        Return True if *dt* falls within the configured business hours for its
        day of the week.

        Parameters
        ----------
        dt:
            Any timezone-aware datetime.  Converted to PKT internally.
        business_hours:
            Mapping keyed by ISO weekday integer (0=Monday … 6=Sunday).
            Each value is a dict with keys:
              - ``"start"``   : ``"HH:MM"`` string
              - ``"end"``     : ``"HH:MM"`` string
              - ``"is_open"`` : bool
            Example::

                {
                    0: {"start": "09:00", "end": "17:00", "is_open": True},
                    6: {"start": "09:00", "end": "13:00", "is_open": True},
                }

        Returns
        -------
        bool
            False when the day is not present in *business_hours*, when
            ``is_open`` is False, or when *dt* (in PKT) is outside
            [start, end).
        """
        pkt_dt = PKTCalendar.to_pkt(dt)
        day_of_week = pkt_dt.weekday()  # 0=Monday … 6=Sunday

        day_config = business_hours.get(day_of_week)
        if day_config is None:
            return False
        if not day_config.get("is_open", False):
            return False

        start_h, start_m = map(int, day_config["start"].split(":"))
        end_h, end_m = map(int, day_config["end"].split(":"))

        slot_time = pkt_dt.time()
        start_time = time(start_h, start_m)
        end_time = time(end_h, end_m)

        return start_time <= slot_time < end_time

    # ------------------------------------------------------------------
    # Holiday helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_holiday(d: date, holidays: list[dict]) -> bool:
        """
        Return True if *d* matches any date in *holidays*.

        Parameters
        ----------
        d:
            The date to check.
        holidays:
            List of dicts with at least a ``"date"`` key in ``"YYYY-MM-DD"``
            format.  Example::

                [{"date": "2025-04-10", "name": "Eid ul-Fitr"}]
        """
        d_str = d.isoformat()
        for entry in holidays:
            if entry.get("date") == d_str:
                return True
        return False

    # ------------------------------------------------------------------
    # Next business day
    # ------------------------------------------------------------------

    @staticmethod
    def get_next_business_day(
        from_date: date,
        business_hours: dict,
        holidays: list[dict],
    ) -> date:
        """
        Return the next calendar date (strictly after *from_date*) that is
        both a business day (per *business_hours*) and not a holiday.

        This method iterates up to 365 days to guard against pathological
        configurations (e.g. a clinic closed every day of the week).

        Raises
        ------
        ValueError
            If no business day is found within 365 days.
        """
        candidate = from_date + timedelta(days=1)
        for _ in range(365):
            # Build a noon datetime in PKT so that is_business_hour has a
            # meaningful time to evaluate the day membership.
            candidate_dt = PKT.localize(
                datetime.combine(candidate, time(12, 0, 0))
            )
            if (
                not PKTCalendar.is_holiday(candidate, holidays)
                and PKTCalendar.is_business_hour(candidate_dt, business_hours)
            ):
                return candidate
            candidate += timedelta(days=1)

        raise ValueError(
            f"No business day found within 365 days after {from_date.isoformat()}"
        )
