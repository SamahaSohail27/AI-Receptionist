from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

import pytz
import redis.asyncio as aioredis
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.ws_manager import ws_manager
from models.appointment import Appointment, SlotReservation
from models.doctor import Doctor

logger = logging.getLogger(__name__)

PKT = pytz.timezone("Asia/Karachi")


class BookingService:
    """
    Core appointment booking engine.

    Double-booking is prevented at three layers:
      1. Redis SETNX lock (fast path, TTL = settings.slot_lock_seconds)
      2. PostgreSQL UniqueConstraint on (doctor_id, slot_start_utc) — safety net
      3. Idempotency key — prevents duplicate bookings from retried requests
    """

    def __init__(self, db: AsyncSession, redis_client: aioredis.Redis) -> None:
        self.db = db
        self.redis = redis_client

    # ------------------------------------------------------------------
    # Slot reservation (distributed lock)
    # ------------------------------------------------------------------

    async def reserve_slot(
        self,
        doctor_id: int,
        slot_start_utc: datetime,
        session_id: str,
    ) -> bool:
        """
        Attempt to acquire a Redis SETNX lock for the given slot.

        Returns True if lock was acquired (slot is now reserved for this session).
        Returns False if the slot is already locked by another session.
        """
        redis_key = f"slot:{doctor_id}:{slot_start_utc.isoformat()}"

        acquired = await self.redis.set(
            redis_key,
            session_id,
            nx=True,  # SETNX semantics
            ex=settings.slot_lock_seconds,
        )

        if not acquired:
            logger.debug(
                "reserve_slot: slot already locked — doctor=%s slot=%s session=%s",
                doctor_id,
                slot_start_utc.isoformat(),
                session_id,
            )
            return False

        # Write durable audit record in DB
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.slot_lock_seconds)
        reservation = SlotReservation(
            doctor_id=doctor_id,
            slot_start_utc=slot_start_utc,
            session_id=session_id,
            expires_at=expires_at,
        )
        self.db.add(reservation)

        try:
            await self.db.flush()
        except IntegrityError:
            # Another session beat us to the DB record (rare race) — honour the Redis lock
            # we already hold and do not treat this as a failure; the DB row is just audit.
            await self.db.rollback()
            logger.warning(
                "reserve_slot: DB reservation row conflict (Redis lock held) — "
                "doctor=%s slot=%s session=%s",
                doctor_id,
                slot_start_utc.isoformat(),
                session_id,
            )

        logger.info(
            "reserve_slot: acquired — doctor=%s slot=%s session=%s",
            doctor_id,
            slot_start_utc.isoformat(),
            session_id,
        )
        return True

    async def release_slot(
        self,
        doctor_id: int,
        slot_start_utc: datetime,
        session_id: str,
    ) -> None:
        """
        Release the Redis lock only if it belongs to this session, then delete
        the corresponding SlotReservation row from the DB.
        """
        redis_key = f"slot:{doctor_id}:{slot_start_utc.isoformat()}"

        # Lua script: atomic check-and-delete (avoids releasing another session's lock)
        lua_script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        released = await self.redis.eval(lua_script, 1, redis_key, session_id)  # type: ignore[arg-type]

        if released:
            logger.info(
                "release_slot: Redis lock released — doctor=%s slot=%s session=%s",
                doctor_id,
                slot_start_utc.isoformat(),
                session_id,
            )
        else:
            logger.debug(
                "release_slot: lock not owned by this session (already expired or different owner) "
                "— doctor=%s slot=%s session=%s",
                doctor_id,
                slot_start_utc.isoformat(),
                session_id,
            )

        # Remove DB reservation row (best-effort — if it was already cleaned up, that is fine)
        result = await self.db.execute(
            select(SlotReservation).where(
                and_(
                    SlotReservation.doctor_id == doctor_id,
                    SlotReservation.slot_start_utc == slot_start_utc,
                    SlotReservation.session_id == session_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await self.db.delete(row)
            await self.db.flush()

    # ------------------------------------------------------------------
    # Booking
    # ------------------------------------------------------------------

    async def book_appointment(
        self,
        patient_id: int,
        doctor_id: int,
        slot_start_utc: datetime,
        appointment_type: str = "consultation",
        booking_source: str = "ai",
        notes: str | None = None,
        idempotency_key: str | None = None,
        session_id: str | None = None,
    ) -> Appointment | None:
        """
        Create a confirmed appointment record.

        Returns the Appointment on success.
        Returns None if a conflicting appointment already exists (double-booking at DB level).

        If idempotency_key is supplied and an appointment with that key already exists,
        the existing appointment is returned immediately without writing any new rows.
        """

        # 1. Idempotency check — return existing record if already booked
        if idempotency_key:
            existing_result = await self.db.execute(
                select(Appointment).where(Appointment.idempotency_key == idempotency_key)
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                logger.info(
                    "book_appointment: idempotent return — key=%s appointment_id=%s",
                    idempotency_key,
                    existing.id,
                )
                return existing

        # 2. Fetch doctor to obtain consultation duration
        doctor_result = await self.db.execute(
            select(Doctor).where(Doctor.id == doctor_id)
        )
        doctor = doctor_result.scalar_one_or_none()
        if doctor is None:
            logger.error("book_appointment: doctor not found — doctor_id=%s", doctor_id)
            return None

        # 3. Compute slot end time
        slot_end_utc = slot_start_utc + timedelta(minutes=doctor.consultation_duration_minutes)

        # 4. Generate idempotency key if not provided
        if not idempotency_key:
            raw = f"{session_id or 'no-session'}:{doctor_id}:{slot_start_utc.isoformat()}"
            idempotency_key = hashlib.sha256(raw.encode()).hexdigest()

        # 5. Build appointment object
        appointment = Appointment(
            patient_id=patient_id,
            doctor_id=doctor_id,
            slot_start_utc=slot_start_utc,
            slot_end_utc=slot_end_utc,
            status="scheduled",
            appointment_type=appointment_type,
            booking_source=booking_source,
            notes=notes,
            idempotency_key=idempotency_key,
        )

        self.db.add(appointment)

        # 6. Flush — raises IntegrityError if UniqueConstraint (doctor_id, slot_start_utc) fires
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            logger.warning(
                "book_appointment: DB conflict (double-booking blocked) — "
                "doctor=%s slot=%s",
                doctor_id,
                slot_start_utc.isoformat(),
            )
            return None

        logger.info(
            "book_appointment: success — appointment_id=%s doctor=%s slot=%s patient=%s",
            appointment.id,
            doctor_id,
            slot_start_utc.isoformat(),
            patient_id,
        )

        # 7. Fire-and-forget WebSocket broadcast — never awaited
        ws_manager.broadcast_fire_and_forget(
            {
                "type": "appointment.booked",
                "appointment_id": appointment.id,
                "doctor_id": doctor_id,
                "slot_start_utc": slot_start_utc.isoformat(),
            }
        )

        return appointment

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancel_appointment(
        self,
        appointment_id: int,
        cancelled_by: str = "patient",
    ) -> bool:
        """
        Mark an appointment as cancelled and broadcast the event.

        Returns True on success, False if the appointment was not found.
        """
        result = await self.db.execute(
            select(Appointment).where(Appointment.id == appointment_id)
        )
        appointment = result.scalar_one_or_none()

        if appointment is None:
            logger.warning(
                "cancel_appointment: appointment not found — id=%s", appointment_id
            )
            return False

        appointment.status = "cancelled"
        appointment.cancelled_at = datetime.now(timezone.utc)
        appointment.cancelled_by = cancelled_by

        await self.db.flush()

        logger.info(
            "cancel_appointment: appointment_id=%s cancelled_by=%s",
            appointment_id,
            cancelled_by,
        )

        ws_manager.broadcast_fire_and_forget(
            {
                "type": "appointment.cancelled",
                "appointment_id": appointment_id,
            }
        )

        return True


# ---------------------------------------------------------------------------
# Conflict detector
# ---------------------------------------------------------------------------


class ConflictDetector:
    """
    O(log n) conflict check using the database index on (doctor_id, slot_start_utc).
    """

    async def has_conflict(
        self,
        doctor_id: int,
        slot_start_utc: datetime,
        db: AsyncSession,
        exclude_appointment_id: int | None = None,
    ) -> bool:
        """
        Return True if a non-cancelled appointment already exists for the given
        doctor and start time (excluding exclude_appointment_id when rescheduling).
        """
        conditions = [
            Appointment.doctor_id == doctor_id,
            Appointment.slot_start_utc == slot_start_utc,
            Appointment.status != "cancelled",
        ]
        if exclude_appointment_id is not None:
            conditions.append(Appointment.id != exclude_appointment_id)

        result = await db.execute(
            select(Appointment.id).where(and_(*conditions)).limit(1)
        )
        return result.scalar_one_or_none() is not None
