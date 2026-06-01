from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests
from celery import Celery
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import settings
from models.appointment import Appointment
from models.audit import NotificationLog
from models.patient import Patient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Celery application
# ---------------------------------------------------------------------------

celery_app = Celery(
    "ai_receptionist",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.timezone = "Asia/Karachi"

# ---------------------------------------------------------------------------
# Beat schedule — periodic tasks
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule = {
    "cleanup-reservations": {
        "task": "slots.cleanup_expired_reservations",
        "schedule": 60.0,  # every 60 seconds
    },
}

# ---------------------------------------------------------------------------
# Synchronous SQLAlchemy engine for use inside Celery tasks
# (asyncpg is not compatible with Celery's sync worker model)
# ---------------------------------------------------------------------------

_sync_engine = create_engine(
    settings.database_url.replace("+asyncpg", "+psycopg2"),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
_SyncSession = sessionmaker(bind=_sync_engine, expire_on_commit=False)


def _get_sync_session() -> Session:
    return _SyncSession()


# ---------------------------------------------------------------------------
# Helper: build reminder message text (PKT context, three languages)
# ---------------------------------------------------------------------------

def _build_reminder_body(
    patient: Patient,
    appointment: Appointment,
    reminder_type: str,
) -> str:
    """
    Return a localised reminder message.
    Respects patient.preferred_language (ur-PK | pa-PK | en).
    """
    # Convert UTC slot to PKT for display
    import pytz

    pkt = pytz.timezone("Asia/Karachi")
    slot_pkt = appointment.slot_start_utc.astimezone(pkt)
    slot_str = slot_pkt.strftime("%d %b %Y, %I:%M %p")  # e.g. 15 Apr 2026, 10:30 AM

    time_label = "24 گھنٹے" if reminder_type == "24h" else "2 گھنٹے"
    time_label_pa = "24 گھنٹے" if reminder_type == "24h" else "2 گھنٹے"
    time_label_en = "24 hours" if reminder_type == "24h" else "2 hours"

    lang = getattr(patient, "preferred_language", "ur-PK") or "ur-PK"

    if lang.startswith("ur"):
        name = patient.name_ur or patient.name_en or "مریض"
        return (
            f"السلام علیکم {name} صاحب/صاحبہ،\n"
            f"آپ کی اپائنٹمنٹ {time_label} بعد ہے۔\n"
            f"وقت: {slot_str} (پاکستان معیاری وقت)\n"
            "براہ کرم وقت پر تشریف لائیں۔"
        )
    elif lang.startswith("pa"):
        name = patient.name_ur or patient.name_en or "مریض"
        return (
            f"السلام علیکم {name},\n"
            f"تہاڈی اپوائنٹمنٹ {time_label_pa} بعد اے۔\n"
            f"ویلا: {slot_str} (پاکستان ویلا)\n"
            "مہربانی کر کے ویلے تے آؤ۔"
        )
    else:
        name = patient.name_en or patient.name_ur or "Patient"
        return (
            f"Dear {name},\n"
            f"Your appointment is in {time_label_en}.\n"
            f"Time: {slot_str} (Pakistan Standard Time)\n"
            "Please arrive on time."
        )


# ---------------------------------------------------------------------------
# Helper: send via Twilio WhatsApp
# ---------------------------------------------------------------------------

def _send_whatsapp_twilio(to_number: str, body: str) -> str:
    """
    Send a WhatsApp message via Twilio Messages API.
    Returns the Twilio message SID on success.
    Raises requests.HTTPError on failure.
    """
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    response = requests.post(
        url,
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        data={
            "From": f"whatsapp:{settings.twilio_whatsapp_from}",
            "To": f"whatsapp:{to_number}",
            "Body": body,
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("sid", "")


# ---------------------------------------------------------------------------
# Helper: send via Plivo SMS
# ---------------------------------------------------------------------------

def _send_sms_plivo(to_number: str, body: str) -> str:
    """
    Send an SMS via Plivo Messages API.
    Returns the Plivo message UUID on success.
    Raises requests.HTTPError on failure.
    """
    url = f"https://api.plivo.com/v1/Account/{settings.plivo_auth_id}/Message/"
    response = requests.post(
        url,
        auth=(settings.plivo_auth_id, settings.plivo_auth_token),
        json={
            "src": settings.plivo_phone_number,
            "dst": to_number,
            "text": body,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    message_uuids = data.get("message_uuid", [])
    return message_uuids[0] if message_uuids else ""


# ---------------------------------------------------------------------------
# Task: send_appointment_reminder
# ---------------------------------------------------------------------------

@celery_app.task(
    name="reminders.send_appointment_reminder",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_appointment_reminder(self, appointment_id: int, reminder_type: str) -> dict:
    """
    Send a reminder (WhatsApp or SMS) to the patient for a given appointment.

    reminder_type: "24h" | "2h"

    Returns a dict with keys: status, channel, appointment_id.
    """
    session: Session = _get_sync_session()
    try:
        appointment: Appointment | None = session.get(Appointment, appointment_id)
        if appointment is None:
            logger.warning(
                "send_appointment_reminder: appointment not found — id=%s", appointment_id
            )
            return {"status": "skipped", "reason": "appointment_not_found", "appointment_id": appointment_id}

        if appointment.status == "cancelled":
            logger.info(
                "send_appointment_reminder: appointment cancelled, skipping — id=%s", appointment_id
            )
            return {"status": "skipped", "reason": "cancelled", "appointment_id": appointment_id}

        patient: Patient | None = session.get(Patient, appointment.patient_id)
        if patient is None:
            logger.warning(
                "send_appointment_reminder: patient not found — patient_id=%s", appointment.patient_id
            )
            return {"status": "skipped", "reason": "patient_not_found", "appointment_id": appointment_id}

        body = _build_reminder_body(patient, appointment, reminder_type)

        channel: str
        provider: str
        sent_ok = False

        if patient.whatsapp_opted_in and patient.phone_e164:
            # WhatsApp via Twilio
            try:
                _send_whatsapp_twilio(patient.phone_e164, body)
                channel = "whatsapp"
                provider = "twilio"
                sent_ok = True
            except Exception as exc:
                logger.error(
                    "send_appointment_reminder: WhatsApp send failed — %s, falling back to SMS", exc
                )
                # Fall through to SMS

        if not sent_ok and patient.phone_e164:
            # SMS via Plivo
            try:
                _send_sms_plivo(patient.phone_e164, body)
                channel = "sms"
                provider = "plivo"
                sent_ok = True
            except Exception as exc:
                logger.error("send_appointment_reminder: SMS send failed — %s", exc)
                raise self.retry(exc=exc)

        if not sent_ok:
            return {"status": "failed", "reason": "no_channel", "appointment_id": appointment_id}

        # Update reminder flags on appointment
        now_utc = datetime.now(timezone.utc)
        if reminder_type == "24h":
            appointment.reminder_24h_sent = True
        elif reminder_type == "2h":
            appointment.reminder_2h_sent = True

        # Write notification log record
        notification = NotificationLog(
            appointment_id=appointment_id,
            patient_id=appointment.patient_id,
            channel=channel,
            provider=provider,
            language=getattr(patient, "preferred_language", "ur-PK") or "ur-PK",
            notification_type=f"reminder_{reminder_type}",
            status="sent",
            sent_at=now_utc,
            created_at=now_utc,
        )
        session.add(notification)
        session.commit()

        logger.info(
            "send_appointment_reminder: sent — appointment_id=%s reminder_type=%s channel=%s",
            appointment_id,
            reminder_type,
            channel,
        )
        return {"status": "sent", "channel": channel, "appointment_id": appointment_id}

    except Exception as exc:
        session.rollback()
        raise exc
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Task: schedule_reminders_for_appointment
# ---------------------------------------------------------------------------

@celery_app.task(name="reminders.schedule_reminders_for_appointment")
def schedule_reminders_for_appointment(appointment_id: int) -> None:
    """
    Inspect an appointment's slot time and schedule reminder tasks at:
      - 24 hours before  (if the slot is more than 24h away)
      -  2 hours before  (if the slot is more than  2h away)
    """
    session: Session = _get_sync_session()
    try:
        appointment: Appointment | None = session.get(Appointment, appointment_id)
        if appointment is None:
            logger.warning(
                "schedule_reminders_for_appointment: appointment not found — id=%s", appointment_id
            )
            return

        if appointment.status == "cancelled":
            logger.info(
                "schedule_reminders_for_appointment: appointment cancelled, skipping — id=%s",
                appointment_id,
            )
            return

        now_utc = datetime.now(timezone.utc)
        slot_start = appointment.slot_start_utc

        # Ensure slot_start is timezone-aware
        if slot_start.tzinfo is None:
            slot_start = slot_start.replace(tzinfo=timezone.utc)

        eta_24h = slot_start - timedelta(hours=24)
        eta_2h = slot_start - timedelta(hours=2)

        if eta_24h > now_utc:
            send_appointment_reminder.apply_async(
                args=[appointment_id, "24h"],
                eta=eta_24h,
            )
            logger.info(
                "schedule_reminders_for_appointment: 24h reminder scheduled — "
                "appointment_id=%s eta=%s",
                appointment_id,
                eta_24h.isoformat(),
            )
        else:
            logger.debug(
                "schedule_reminders_for_appointment: 24h window already passed — appointment_id=%s",
                appointment_id,
            )

        if eta_2h > now_utc:
            send_appointment_reminder.apply_async(
                args=[appointment_id, "2h"],
                eta=eta_2h,
            )
            logger.info(
                "schedule_reminders_for_appointment: 2h reminder scheduled — "
                "appointment_id=%s eta=%s",
                appointment_id,
                eta_2h.isoformat(),
            )
        else:
            logger.debug(
                "schedule_reminders_for_appointment: 2h window already passed — appointment_id=%s",
                appointment_id,
            )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Task: cleanup_expired_reservations  (Celery Beat — every 60 s)
# ---------------------------------------------------------------------------

@celery_app.task(name="slots.cleanup_expired_reservations")
def cleanup_expired_reservations() -> int:
    """
    Delete SlotReservation rows whose expires_at timestamp has passed.
    Registered in beat_schedule to run every 60 seconds.

    Returns the number of rows deleted.
    """
    from models.appointment import SlotReservation  # local import to avoid circular at module load

    session: Session = _get_sync_session()
    try:
        now_utc = datetime.now(timezone.utc)
        expired = (
            session.query(SlotReservation)
            .filter(SlotReservation.expires_at < now_utc)
            .all()
        )
        count = len(expired)
        for row in expired:
            session.delete(row)
        session.commit()

        if count:
            logger.info("cleanup_expired_reservations: deleted %s expired rows", count)
        return count
    except Exception as exc:
        session.rollback()
        logger.error("cleanup_expired_reservations: error — %s", exc)
        raise
    finally:
        session.close()
