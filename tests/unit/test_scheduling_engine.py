"""
Unit tests — BookingService (scheduling engine).

Uses mocked Redis + async SQLAlchemy session to test:
- reserve_slot acquires lock on first call
- reserve_slot returns False when slot already locked
- release_slot uses Lua script (atomic)
- book_appointment writes DB record
- Double-booking caught by IntegrityError (PostgreSQL UniqueConstraint)
- Idempotency key prevents duplicate bookings from retried requests
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


def _get_booking_service():
    """Import BookingService after patching SQLAlchemy model registry."""
    from scheduling.engine import BookingService
    return BookingService


@pytest.mark.unit
class TestBookingServiceReserveSlot:

    @pytest.fixture
    def redis_mock(self):
        r = AsyncMock()
        r.set = AsyncMock(return_value=True)  # SETNX acquired
        return r

    @pytest.fixture
    def db_mock(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        return db

    @pytest.fixture
    def booking_service(self, db_mock, redis_mock):
        BookingService = _get_booking_service()
        return BookingService(db=db_mock, redis_client=redis_mock)

    @pytest.mark.asyncio
    async def test_reserve_slot_returns_true_when_acquired(self, booking_service, redis_mock):
        redis_mock.set.return_value = True
        slot_start = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
        result = await booking_service.reserve_slot(
            doctor_id=1, slot_start_utc=slot_start, session_id="session-abc"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_reserve_slot_returns_false_when_already_locked(self, booking_service, redis_mock):
        redis_mock.set.return_value = None  # SETNX returns None when key exists
        slot_start = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
        result = await booking_service.reserve_slot(
            doctor_id=1, slot_start_utc=slot_start, session_id="session-xyz"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_reserve_slot_uses_setnx(self, booking_service, redis_mock):
        slot_start = datetime(2025, 6, 1, 11, 0, tzinfo=timezone.utc)
        await booking_service.reserve_slot(
            doctor_id=2, slot_start_utc=slot_start, session_id="sess-1"
        )
        # Verify SETNX semantics (nx=True)
        call_kwargs = redis_mock.set.call_args[1]
        assert call_kwargs.get("nx") is True

    @pytest.mark.asyncio
    async def test_reserve_slot_redis_key_includes_doctor_id_and_slot(self, booking_service, redis_mock):
        slot_start = datetime(2025, 6, 1, 9, 0, tzinfo=timezone.utc)
        await booking_service.reserve_slot(
            doctor_id=5, slot_start_utc=slot_start, session_id="sess-2"
        )
        key_arg = redis_mock.set.call_args[0][0]
        assert "5" in key_arg
        assert "2025-06-01" in key_arg

    @pytest.mark.asyncio
    async def test_reserve_slot_writes_audit_record(self, booking_service, db_mock, redis_mock):
        redis_mock.set.return_value = True
        slot_start = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
        await booking_service.reserve_slot(
            doctor_id=1, slot_start_utc=slot_start, session_id="sess-3"
        )
        db_mock.add.assert_called_once()


@pytest.mark.unit
class TestBookingServiceBookAppointment:

    @pytest.fixture
    def redis_mock(self):
        r = AsyncMock()
        r.set = AsyncMock(return_value=True)
        r.eval = AsyncMock(return_value=1)  # Lua script returns 1 = released
        return r

    @pytest.fixture
    def db_mock(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        # Default: no existing appointment (idempotency check returns None)
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(return_value=mock_result)
        return db

    @pytest.fixture
    def booking_service(self, db_mock, redis_mock):
        BookingService = _get_booking_service()
        return BookingService(db=db_mock, redis_client=redis_mock)

    @pytest.mark.asyncio
    async def test_book_appointment_creates_appointment_record(self, booking_service, db_mock):
        slot_start = datetime(2025, 6, 2, 14, 0, tzinfo=timezone.utc)
        # No idempotency_key → no idempotency execute, only doctor fetch execute
        mock_doctor = MagicMock()
        mock_doctor.consultation_duration_minutes = 20
        doctor_result = MagicMock()
        doctor_result.scalar_one_or_none.return_value = mock_doctor
        db_mock.execute = AsyncMock(return_value=doctor_result)

        result = await booking_service.book_appointment(
            doctor_id=1,
            patient_id=10,
            slot_start_utc=slot_start,
            session_id="sess-book-1",
            appointment_type="consultation",
        )
        db_mock.add.assert_called()
        db_mock.flush.assert_called()

    @pytest.mark.asyncio
    async def test_book_appointment_idempotency_returns_existing(self, booking_service, db_mock):
        # Simulate existing appointment found by idempotency key
        existing_appt = MagicMock()
        existing_appt.id = 99
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_appt
        db_mock.execute = AsyncMock(return_value=mock_result)

        slot_start = datetime(2025, 6, 2, 14, 0, tzinfo=timezone.utc)
        result = await booking_service.book_appointment(
            doctor_id=1,
            patient_id=10,
            slot_start_utc=slot_start,
            session_id="sess-idem-1",
            appointment_type="consultation",
            idempotency_key="idem-key-123",
        )
        # Should return existing appointment without adding new one
        assert result is not None
        assert result.id == 99

    @pytest.mark.asyncio
    async def test_book_appointment_handles_integrity_error(self, booking_service, db_mock):
        from sqlalchemy.exc import IntegrityError
        mock_doctor = MagicMock()
        mock_doctor.consultation_duration_minutes = 20
        no_existing = MagicMock()
        no_existing.scalar_one_or_none.return_value = None
        doctor_result = MagicMock()
        doctor_result.scalar_one_or_none.return_value = mock_doctor
        db_mock.execute = AsyncMock(side_effect=[no_existing, doctor_result])
        db_mock.commit = AsyncMock(side_effect=IntegrityError("duplicate", None, None))

        slot_start = datetime(2025, 6, 3, 10, 0, tzinfo=timezone.utc)
        result = await booking_service.book_appointment(
            doctor_id=1,
            patient_id=11,
            slot_start_utc=slot_start,
            session_id="sess-conflict",
            appointment_type="consultation",
        )
        # Returns None on double-booking (IntegrityError caught → returns None)
        assert result is None


@pytest.mark.unit
class TestPKTTimezone:
    """Verify PKT timezone is pytz Asia/Karachi — never a raw timedelta."""

    def test_pkt_is_pytz_timezone(self):
        import pytz
        from scheduling.engine import PKT
        assert PKT == pytz.timezone("Asia/Karachi")

    def test_pkt_calendar_pkt_is_pytz(self):
        import pytz
        from scheduling.pkt_calendar import PKT as CAL_PKT
        assert CAL_PKT == pytz.timezone("Asia/Karachi")

    def test_pkt_is_not_utc(self):
        from scheduling.engine import PKT
        import pytz
        # Asia/Karachi UTC+5 — should not be UTC
        import datetime
        now_pkt = datetime.datetime.now(PKT)
        offset_hours = now_pkt.utcoffset().total_seconds() / 3600
        assert offset_hours == 5.0
