from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime

import redis.asyncio as aioredis

from core.ws_manager import ws_manager

logger = logging.getLogger(__name__)


@dataclass
class WaitlistEntry:
    patient_id: int
    doctor_id: int
    preferred_date: datetime | None
    appointment_type: str
    language: str
    whatsapp_number: str | None
    created_at: datetime


def _entry_to_json(entry: WaitlistEntry) -> str:
    """Serialize WaitlistEntry to JSON string, converting datetimes to ISO strings."""
    data = asdict(entry)
    if data["preferred_date"] is not None:
        data["preferred_date"] = entry.preferred_date.isoformat()  # type: ignore[union-attr]
    data["created_at"] = entry.created_at.isoformat()
    return json.dumps(data)


def _entry_from_json(raw: str) -> WaitlistEntry:
    """Deserialize WaitlistEntry from a JSON string stored in Redis."""
    data = json.loads(raw)
    preferred_date: datetime | None = None
    if data["preferred_date"] is not None:
        preferred_date = datetime.fromisoformat(data["preferred_date"])
    created_at = datetime.fromisoformat(data["created_at"])
    return WaitlistEntry(
        patient_id=data["patient_id"],
        doctor_id=data["doctor_id"],
        preferred_date=preferred_date,
        appointment_type=data["appointment_type"],
        language=data["language"],
        whatsapp_number=data["whatsapp_number"],
        created_at=created_at,
    )


def _redis_key(doctor_id: int) -> str:
    return f"waitlist:{doctor_id}"


class WaitlistManager:
    """
    Manages per-doctor waitlists using a Redis sorted set.

    Key:   waitlist:{doctor_id}
    Score: entry.created_at.timestamp()   (earliest = lowest score = first in queue)
    Value: JSON-serialised WaitlistEntry

    When a slot opens, call notify_next_patient() to pop and alert the
    earliest-queued patient.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self.redis = redis_client

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    async def add_to_waitlist(self, entry: WaitlistEntry) -> int:
        """
        Add a patient to the waitlist for the given doctor.

        Returns the patient's queue position (1-based rank).
        If the patient is already in the queue, their entry is updated and
        their position recalculated.
        """
        key = _redis_key(entry.doctor_id)
        score = entry.created_at.timestamp()
        value = _entry_to_json(entry)

        await self.redis.zadd(key, {value: score})

        # Rank is 0-based from the sorted set; convert to 1-based position
        rank = await self.redis.zrank(key, value)
        position = (rank or 0) + 1

        logger.info(
            "add_to_waitlist: patient=%s doctor=%s position=%s",
            entry.patient_id,
            entry.doctor_id,
            position,
        )
        return position

    async def remove_from_waitlist(self, patient_id: int, doctor_id: int) -> bool:
        """
        Remove a patient from the waitlist.

        Returns True if the patient was found and removed, False otherwise.
        """
        key = _redis_key(doctor_id)
        # Scan all members and find the one belonging to this patient_id
        members: list[bytes] = await self.redis.zrange(key, 0, -1)

        for raw in members:
            decoded = raw.decode() if isinstance(raw, bytes) else raw
            try:
                data = json.loads(decoded)
            except json.JSONDecodeError:
                continue
            if data.get("patient_id") == patient_id:
                removed = await self.redis.zrem(key, decoded)
                if removed:
                    logger.info(
                        "remove_from_waitlist: patient=%s doctor=%s removed",
                        patient_id,
                        doctor_id,
                    )
                    return True

        logger.debug(
            "remove_from_waitlist: patient=%s doctor=%s not found",
            patient_id,
            doctor_id,
        )
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_position(self, patient_id: int, doctor_id: int) -> int | None:
        """
        Return the 1-based queue position of the patient, or None if not queued.
        """
        key = _redis_key(doctor_id)
        members: list[bytes] = await self.redis.zrange(key, 0, -1)

        for idx, raw in enumerate(members):
            decoded = raw.decode() if isinstance(raw, bytes) else raw
            try:
                data = json.loads(decoded)
            except json.JSONDecodeError:
                continue
            if data.get("patient_id") == patient_id:
                return idx + 1  # 1-based

        return None

    async def get_next_in_queue(self, doctor_id: int) -> WaitlistEntry | None:
        """
        Return the entry with the lowest score (earliest created_at) without
        removing it from the queue.
        """
        key = _redis_key(doctor_id)
        # zrange with byscore / withscores is not needed — first element has lowest score
        members: list[bytes] = await self.redis.zrange(key, 0, 0)

        if not members:
            return None

        raw = members[0]
        decoded = raw.decode() if isinstance(raw, bytes) else raw
        try:
            return _entry_from_json(decoded)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error("get_next_in_queue: failed to deserialise entry — %s", exc)
            return None

    # ------------------------------------------------------------------
    # Notification
    # ------------------------------------------------------------------

    async def notify_next_patient(
        self, doctor_id: int, slot_start_utc: datetime
    ) -> bool:
        """
        Notify the next patient in the waitlist that a slot has become available.

        Fires a WebSocket event (fire-and-forget).
        In production this would also dispatch a Celery task to send SMS/WhatsApp.

        Returns True if a patient was notified, False if the waitlist is empty.
        """
        entry = await self.get_next_in_queue(doctor_id)
        if entry is None:
            logger.info(
                "notify_next_patient: waitlist empty — doctor=%s slot=%s",
                doctor_id,
                slot_start_utc.isoformat(),
            )
            return False

        logger.info(
            "notify_next_patient: notifying patient=%s doctor=%s slot=%s",
            entry.patient_id,
            doctor_id,
            slot_start_utc.isoformat(),
        )

        ws_manager.broadcast_fire_and_forget(
            {
                "type": "waitlist.slot_available",
                "doctor_id": doctor_id,
                "patient_id": entry.patient_id,
                "slot_start_utc": slot_start_utc.isoformat(),
            }
        )

        # Production extension point:
        # from tasks import send_slot_available_notification
        # send_slot_available_notification.delay(
        #     patient_id=entry.patient_id,
        #     doctor_id=doctor_id,
        #     slot_start_utc=slot_start_utc.isoformat(),
        #     whatsapp_number=entry.whatsapp_number,
        #     language=entry.language,
        # )

        return True
