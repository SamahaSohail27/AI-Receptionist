"""
Browser-driven test session — LLM with REAL booking tools.

This is the same conversational test harness from before, but the LLM now has
function-calling tools wired to the live database via the existing
``AvailabilityEngine`` and ``BookingService``. So when the assistant says
"booked for tomorrow 4pm", a real Appointment row is written and the
appointments page / doctor portal will reflect it immediately.

Tools exposed:
  - list_doctors(specialty?)            → real Doctor rows, active only
  - find_available_slots(doctor_id,     → AvailabilityEngine over PKT date
                         date_pkt)         (DoctorAvailability + bookings)
  - find_or_create_patient(name, phone) → upsert by phone_e164
  - book_appointment(patient_id,        → BookingService.book_appointment
                     doctor_id,
                     date_pkt, time_pkt)

Authentication: every endpoint and every tool call runs as the logged-in
staff user. Bookings are tagged ``booking_source="ai-test"``.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import uuid
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Optional

# Pakistani E.164 mobile: +92 followed by 10 digits, leading 3 (mobile prefix).
_PK_E164_RE = re.compile(r"^\+92\d{10}$")

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import require_any_staff
from core.database import get_db
from core.ws_manager import ws_manager
from models.auth import User
from models.call_log import CallLog, Transcript
from models.doctor import Doctor
from models.patient import Patient

# OpenAI gpt-4o-mini and tts-1 / whisper-1 pricing (USD).
_LLM_INPUT_USD_PER_TOKEN = 0.15 / 1_000_000
_LLM_OUTPUT_USD_PER_TOKEN = 0.60 / 1_000_000
_TTS_USD_PER_CHAR = 15.0 / 1_000_000
_WHISPER_USD_PER_MINUTE = 0.006

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/test-session", tags=["test-session"])

LanguageCode = Literal["ur-PK", "pa-PK", "en"]

_PKT_OFFSET = timedelta(hours=5)


def _now_pkt() -> datetime:
    return datetime.now(timezone.utc) + _PKT_OFFSET


# ---------------------------------------------------------------------------
# Per-language receptionist persona (shared base behavior is appended below)
# ---------------------------------------------------------------------------

_LANG_CONFIG: dict[str, dict[str, str]] = {
    "ur-PK": {
        "persona": (
            "You are a Pakistani medical clinic receptionist. Speak ONLY in "
            "Pakistani Urdu (ur-PK). Use Pakistani vocabulary — never Indian "
            "Urdu or Hindi loanwords."
        ),
        "greeting": "السلام علیکم، یہ کلینک کی ریسپشن ہے۔ میں آپ کی کیا مدد کر سکتی ہوں؟",
        "voice": "shimmer",
        "whisper_lang": "ur",
    },
    "pa-PK": {
        "persona": (
            "You are a Pakistani medical clinic receptionist. Speak ONLY in "
            "Pakistani Punjabi (pa-PK), Shahmukhi script."
        ),
        "greeting": "السلام علیکم، کلینک دی ریسپشن توں گل ہو رہی اے۔ میں تہاڈی کی مدد کراں؟",
        "voice": "shimmer",
        "whisper_lang": "pa",
    },
    "en": {
        "persona": (
            "You are a friendly receptionist at a Pakistani medical clinic. "
            "Speak in clear, simple English."
        ),
        "greeting": "Hello, you've reached the clinic reception. How can I help you today?",
        "voice": "nova",
        "whisper_lang": "en",
    },
}


def _system_prompt(language: LanguageCode) -> str:
    cfg = _LANG_CONFIG[language]
    today_pkt = _now_pkt().date()
    weekday = today_pkt.strftime("%A")
    return (
        f"{cfg['persona']}\n\n"
        f"You are Amina — a calm, warm Pakistani clinic receptionist. Today is "
        f"{today_pkt.isoformat()} ({weekday}) in PKT (Asia/Karachi). All times "
        "you discuss with the caller are PKT (24-hour clock).\n\n"
        "TONE — natural, empathetic, conversational. Vary phrasing across turns. "
        "Acknowledge feelings before asking a question ('I'm sorry to hear that'). "
        "ONE question per turn. Replies under 30 words. No clinical advice.\n"
        "Pakistani Urdu only — never Indian-Urdu vocabulary or Hindi loanwords.\n\n"
        "SCENARIOS — handle these naturally:\n"
        "  - General booking: collect doctor preference, name, phone, then book.\n"
        "  - Urgency cues ('severe', 'very bad', 'unbearable'): empathise, call "
        "    `triage_severity` first, then call `find_earliest_slot` with "
        "    urgency='urgent'. Offer the soonest slot today/tomorrow.\n"
        "  - Emergency cues ('chest pain', 'can't breathe', 'unconscious', "
        "    'bleeding heavily'): tell them to call 1122 immediately and DO NOT "
        "    book; mark via triage_severity which will return severity='emergency'.\n"
        "  - Female-doctor preference: call `search_doctors(gender='F', ...)`. "
        "    Same for male.\n"
        "  - Specialist requests: filter `search_doctors` by specialty.\n"
        "  - Reschedule: call `find_existing_appointment(phone)`, then "
        "    `reschedule_appointment(appointment_id, ...)`.\n"
        "  - Cancel: same lookup, then `cancel_appointment_tool(appointment_id)`.\n"
        "  - Time-specific ('tomorrow evening'): convert to a PKT date + "
        "    approximate time, find slots, offer the closest matches.\n"
        "  - Confused/incomplete input ('not feeling well'): ask ONE clarifying "
        "    question — what symptom, or what doctor they prefer.\n\n"
        "TOOLS — you MUST use them; never invent doctors, slots, or appointment IDs.\n"
        "- `search_doctors(specialty?, gender?)` lists active doctors.\n"
        "- `find_available_slots(doctor_id, date_pkt)` — required before "
        "  promising any specific time.\n"
        "- `find_earliest_slot(doctor_id?, specialty?, urgency)` — fastest path "
        "  for urgent cases or 'soonest possible' requests.\n"
        "- `triage_severity(symptom_text)` — call early when caller mentions a "
        "  symptom; you'll get back severity + a suggested action.\n"
        "- `find_or_create_patient(name, phone)` — phone in +92 E.164 format.\n"
        "- `book_appointment(patient_id, doctor_id, date_pkt, time_pkt)`.\n"
        "- `find_existing_appointment(phone)` — for reschedule/cancel flows.\n"
        "- `reschedule_appointment(appointment_id, date_pkt, time_pkt)`.\n"
        "- `cancel_appointment_tool(appointment_id)`.\n\n"
        "HARD RULES — never break these:\n"
        "1. You are FORBIDDEN from saying any specific date or time until "
        "`find_available_slots` (or `find_earliest_slot`) has returned for "
        "the exact doctor + date you are about to mention. No rounding, no "
        "approximation, no 'around 4pm'. Only times that were literally in the "
        "returned list.\n"
        "2. Collect the phone number from the caller and pass it to "
        "`find_or_create_patient` exactly as they said it — local "
        "(03XX-XXXXXXX), international (+92...), or with spaces/dashes are "
        "all fine. The tool normalizes it. Only ask for the number again if "
        "the tool returns an `error` field (too few digits to be a number). "
        "If it returns a `phone_warning`, accept it silently and proceed; "
        "the clinic will verify offline. Do NOT loop the caller for "
        "re-entry when the tool already returned a patient_id.\n"
        "3. Once `book_appointment` returns an appointment_id, the booking "
        "thread is CLOSED. Confirm doctor + date + time + appointment number, "
        "then ask if there's anything else. NEVER bring back an earlier "
        "specialty, alternative doctor, or 'no one available' line — those "
        "threads are resolved the moment a booking succeeds.\n"
        "4. Speech recognition sometimes drops words or hallucinates. If a "
        "user message is empty, garbled, or in an unexpected language, ask "
        "them to repeat. Never silently assume a specialty from a partial "
        "transcript.\n\n"
        "AFTER BOOKING — confirm doctor name, date and time, read out the "
        "appointment number, and offer to send WhatsApp confirmation.\n"
    )


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_doctors",
            "description": (
                "List active doctors at the clinic. Optionally filter by a "
                "specialty keyword (e.g. 'cardiology', 'pediatrics', 'ENT')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "specialty": {
                        "type": "string",
                        "description": "Optional substring match against doctor.speciality",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_doctors",
            "description": (
                "Search active doctors with optional gender + specialty filters. "
                "Use this when the caller asks for a 'female doctor', a 'male "
                "specialist', or names a specialty + preference."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "specialty": {"type": "string"},
                    "gender": {"type": "string", "enum": ["F", "M"]},
                    "name": {"type": "string", "description": "Substring match on doctor name_en"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_earliest_slot",
            "description": (
                "Return the soonest available appointment slot starting from "
                "today. Use when the caller says 'as soon as possible', "
                "'earliest', or describes urgency ('severe headache')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor_id": {"type": "integer"},
                    "specialty": {"type": "string"},
                    "gender": {"type": "string", "enum": ["F", "M"]},
                    "urgency": {
                        "type": "string",
                        "enum": ["normal", "urgent"],
                        "description": "When 'urgent', look only at today + tomorrow",
                    },
                    "max_days_ahead": {
                        "type": "integer",
                        "description": "Default 14; capped at 30",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "triage_severity",
            "description": (
                "Classify symptom severity (low / medium / high / emergency) "
                "and return a suggested action. Call this BEFORE booking when "
                "the caller mentions a symptom or pain. If severity='emergency' "
                "you MUST tell the caller to call 1122 and not book."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symptom_text": {"type": "string"},
                    "language": {"type": "string", "enum": ["en", "ur-PK", "pa-PK"]},
                },
                "required": ["symptom_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_existing_appointment",
            "description": (
                "Look up the most recent active (scheduled/confirmed) "
                "appointment for a patient by phone. Use before reschedule or "
                "cancel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "+92… E.164"},
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": "Move an existing appointment to a new PKT slot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "integer"},
                    "date_pkt": {"type": "string", "description": "YYYY-MM-DD"},
                    "time_pkt": {"type": "string", "description": "HH:MM 24h"},
                },
                "required": ["appointment_id", "date_pkt", "time_pkt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment_tool",
            "description": "Cancel an existing appointment by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["appointment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_available_slots",
            "description": (
                "Return ALL bookable start times for a doctor on a specific PKT "
                "calendar date. Times are 24-hour HH:MM. CALL THIS BEFORE "
                "PROMISING ANY APPOINTMENT TIME — never assume availability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor_id": {"type": "integer"},
                    "date_pkt": {
                        "type": "string",
                        "description": "PKT calendar date YYYY-MM-DD",
                    },
                },
                "required": ["doctor_id", "date_pkt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_or_create_patient",
            "description": (
                "Look up an existing patient by phone (E.164). Create a new "
                "patient if not found. Returns the patient_id you must pass to "
                "book_appointment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "phone": {
                        "type": "string",
                        "description": "Phone in E.164 format, e.g. +923001234567",
                    },
                    "preferred_language": {
                        "type": "string",
                        "enum": ["ur-PK", "pa-PK", "en"],
                    },
                },
                "required": ["name", "phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Create a confirmed appointment. Only call AFTER "
                "find_available_slots has confirmed the slot and "
                "find_or_create_patient has returned a patient_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "integer"},
                    "doctor_id": {"type": "integer"},
                    "date_pkt": {"type": "string", "description": "YYYY-MM-DD"},
                    "time_pkt": {
                        "type": "string",
                        "description": "HH:MM, 24-hour PKT (e.g. 16:00 for 4pm)",
                    },
                    "notes": {"type": "string"},
                },
                "required": ["patient_id", "doctor_id", "date_pkt", "time_pkt"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations (DB-backed)
# ---------------------------------------------------------------------------

async def _tool_list_doctors(db: AsyncSession, *, specialty: Optional[str] = None) -> dict:
    stmt = select(Doctor).where(Doctor.is_active.is_(True))
    if specialty:
        stmt = stmt.where(Doctor.speciality.ilike(f"%{specialty.strip()}%"))
    result = await db.execute(stmt.order_by(Doctor.id))
    docs = result.scalars().all()
    return {
        "doctors": [
            {
                "id": d.id,
                "name": d.name_en,
                "name_ur": d.name_ur,
                "specialty": d.speciality,
                "consultation_minutes": d.consultation_duration_minutes,
                "fee_pkr": d.consultation_fee,
                "room": d.room_number,
            }
            for d in docs
        ]
    }


async def _tool_find_available_slots(
    db: AsyncSession, *, doctor_id: int, date_pkt: str
) -> dict:
    from scheduling.availability import AvailabilityEngine

    try:
        d = date_cls.fromisoformat(date_pkt)
    except ValueError:
        return {"error": f"invalid date_pkt={date_pkt!r} (expected YYYY-MM-DD)"}

    engine = AvailabilityEngine(db)
    slots = await engine.get_available_slots(doctor_id, d, d)
    times = sorted(
        (s.start_utc + _PKT_OFFSET).strftime("%H:%M")
        for s in slots
        if s.is_available
    )
    return {
        "doctor_id": doctor_id,
        "date_pkt": date_pkt,
        "available_times_pkt": times,
        "slot_count": len(times),
    }


async def _tool_find_or_create_patient(
    db: AsyncSession,
    *,
    name: str,
    phone: str,
    preferred_language: str = "ur-PK",
) -> dict:
    # Best-effort phone normalization. Accepts any common Pakistani format
    # the STT might produce — 03XX-XXXXXXX local, +92 international,
    # 92XX without +, spaces/dashes/parentheses, even slightly short or
    # long inputs — and only errors out when the result clearly is not a
    # phone number (fewer than 7 digits). Whisper-1 frequently drops or
    # adds a digit on short Urdu clips, so being strict here just blocks
    # bookings forever.
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    # Trim country code if present.
    if digits.startswith("92"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]

    if len(digits) < 7:
        return {
            "error": (
                f"phone {phone!r} doesn't look like a complete phone number "
                "(too few digits). Ask the caller to repeat their full mobile "
                "number digit-by-digit."
            ),
            "received": raw,
            "digits_found": digits,
        }

    phone_norm = "+92" + digits
    phone_warning: Optional[str] = None
    if len(digits) != 10:
        # 10 digits after +92 is the canonical PK mobile shape (3XX-XXXXXXX).
        # Anything else is suspicious but we still proceed — book the patient
        # and flag for clinic staff follow-up.
        phone_warning = (
            f"Phone has {len(digits)} digits after country code "
            "(expected 10 for a Pakistani mobile). Booking will proceed "
            "but please verify the number with the caller."
        )

    result = await db.execute(select(Patient).where(Patient.phone_e164 == phone_norm))
    patient = result.scalar_one_or_none()

    created = False
    if patient is None:
        patient = Patient(
            name_en=name.strip() or None,
            phone_e164=phone_norm,
            preferred_language=preferred_language,
        )
        db.add(patient)
        await db.flush()
        await db.commit()
        await db.refresh(patient)
        created = True
    elif name and not patient.name_en:
        patient.name_en = name.strip()
        await db.commit()
        await db.refresh(patient)

    out: dict = {
        "patient_id": patient.id,
        "created": created,
        "name": patient.name_en or patient.name_ur,
        "phone_e164": patient.phone_e164,
    }
    if phone_warning:
        out["phone_warning"] = phone_warning
    return out


async def _tool_book_appointment(
    db: AsyncSession,
    *,
    patient_id: int,
    doctor_id: int,
    date_pkt: str,
    time_pkt: str,
    notes: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict:
    import redis.asyncio as aioredis

    from core.config import settings as _settings
    from scheduling.engine import BookingService

    try:
        d = date_cls.fromisoformat(date_pkt)
        hh, mm = (int(p) for p in time_pkt.strip().split(":")[:2])
    except Exception:
        return {"error": f"invalid date/time: date_pkt={date_pkt!r} time_pkt={time_pkt!r}"}

    # PKT wall-clock → UTC datetime
    slot_pkt_naive = datetime(d.year, d.month, d.day, hh, mm, 0)
    slot_utc = (slot_pkt_naive - _PKT_OFFSET).replace(tzinfo=timezone.utc)

    redis_url = getattr(_settings, "redis_url", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    redis_client = aioredis.from_url(redis_url)
    service = BookingService(db, redis_client)

    appt = await service.book_appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        slot_start_utc=slot_utc,
        booking_source="ai-test",
        notes=notes,
        session_id=session_id,
    )
    if appt is None:
        return {
            "error": (
                "could not book — slot conflict or doctor not found. "
                "Re-check find_available_slots and try a different time."
            )
        }
    await db.commit()
    return {
        "appointment_id": appt.id,
        "status": "scheduled",
        "doctor_id": doctor_id,
        "patient_id": patient_id,
        "slot_pkt": f"{date_pkt} {time_pkt}",
        "slot_start_utc": appt.slot_start_utc.isoformat(),
        "slot_end_utc": appt.slot_end_utc.isoformat(),
    }


async def _tool_search_doctors(
    db: AsyncSession,
    *,
    specialty: Optional[str] = None,
    gender: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    stmt = select(Doctor).where(Doctor.is_active.is_(True))
    if specialty:
        stmt = stmt.where(Doctor.speciality.ilike(f"%{specialty.strip()}%"))
    if gender and gender.upper() in ("F", "M"):
        stmt = stmt.where(Doctor.gender == gender.upper())
    if name:
        stmt = stmt.where(Doctor.name_en.ilike(f"%{name.strip()}%"))
    result = await db.execute(stmt.order_by(Doctor.id))
    docs = result.scalars().all()
    return {
        "doctors": [
            {
                "id": d.id,
                "name": d.name_en,
                "specialty": d.speciality,
                "gender": d.gender,
                "consultation_minutes": d.consultation_duration_minutes,
                "fee_pkr": d.consultation_fee,
                "room": d.room_number,
            }
            for d in docs
        ],
        "filters": {"specialty": specialty, "gender": gender, "name": name},
    }


async def _tool_find_earliest_slot(
    db: AsyncSession,
    *,
    doctor_id: Optional[int] = None,
    specialty: Optional[str] = None,
    gender: Optional[str] = None,
    urgency: str = "normal",
    max_days_ahead: int = 14,
) -> dict:
    """Walk forward day by day until we find an open slot."""
    from scheduling.availability import AvailabilityEngine

    days_cap = 2 if urgency == "urgent" else max(1, min(int(max_days_ahead or 14), 30))

    stmt = select(Doctor).where(Doctor.is_active.is_(True))
    if doctor_id:
        stmt = stmt.where(Doctor.id == doctor_id)
    if specialty:
        stmt = stmt.where(Doctor.speciality.ilike(f"%{specialty.strip()}%"))
    if gender and gender.upper() in ("F", "M"):
        stmt = stmt.where(Doctor.gender == gender.upper())

    candidates = (await db.execute(stmt.order_by(Doctor.id))).scalars().all()
    if not candidates:
        return {"found": False, "reason": "No doctors match the requested filters."}

    engine = AvailabilityEngine(db)
    today_pkt = _now_pkt().date()
    for offset in range(days_cap + 1):
        target = today_pkt + timedelta(days=offset)
        for doc in candidates:
            slots = await engine.get_available_slots(doc.id, target, target)
            free = sorted(
                (s.start_utc + _PKT_OFFSET).strftime("%H:%M")
                for s in slots
                if s.is_available
            )
            if free:
                return {
                    "found": True,
                    "doctor_id": doc.id,
                    "doctor_name": doc.name_en,
                    "specialty": doc.speciality,
                    "gender": doc.gender,
                    "date_pkt": target.isoformat(),
                    "earliest_time_pkt": free[0],
                    "more_times_pkt": free[1:6],
                    "urgency": urgency,
                }
    return {
        "found": False,
        "reason": f"No available slots in the next {days_cap} day(s).",
        "urgency": urgency,
    }


# Triage severity — rule-based phrase classifier.
_EMERGENCY_PHRASES: dict[str, tuple[str, ...]] = {
    "en": ("chest pain", "can't breathe", "cant breathe", "unconscious",
           "bleeding heavily", "stroke", "heart attack", "passed out",
           "no pulse", "seizure", "choking"),
    "ur-PK": ("سینے میں درد", "سانس نہیں آ", "بے ہوش", "خون بہہ رہا",
             "دل کا دورہ", "فالج"),
    "pa-PK": ("سینے وچ درد", "سا نہیں آ", "بے ہوش", "خون نکل"),
}
_HIGH_PHRASES: dict[str, tuple[str, ...]] = {
    "en": ("severe", "very bad", "unbearable", "excruciating",
           "extreme pain", "agony", "won't stop", "hours of pain"),
    "ur-PK": ("شدید", "بہت زیادہ", "ناقابل برداشت", "ناقابلِ برداشت",
             "بہت تکلیف"),
    "pa-PK": ("بہت تکلیف", "ناقابل برداشت", "بہت زیادہ"),
}


async def _tool_triage_severity(
    db: AsyncSession,
    *,
    symptom_text: str,
    language: str = "en",
) -> dict:
    text = (symptom_text or "").strip().lower()
    lang = language if language in _EMERGENCY_PHRASES else "en"
    # Emergency wins over high.
    for phrase in _EMERGENCY_PHRASES.get(lang, ()) + _EMERGENCY_PHRASES["en"]:
        if phrase.lower() in text:
            return {
                "severity": "emergency",
                "matched": phrase,
                "suggested_action": (
                    "Tell the caller to dial 1122 immediately. Do NOT book. "
                    "Offer to transfer to the triage nurse."
                ),
            }
    for phrase in _HIGH_PHRASES.get(lang, ()) + _HIGH_PHRASES["en"]:
        if phrase.lower() in text:
            return {
                "severity": "high",
                "matched": phrase,
                "suggested_action": (
                    "Empathise, then call find_earliest_slot(urgency='urgent'). "
                    "Offer the soonest slot today or tomorrow."
                ),
            }
    return {
        "severity": "low",
        "suggested_action": (
            "Proceed with normal booking flow — ask for doctor preference, "
            "name, and phone."
        ),
    }


async def _tool_find_existing_appointment(
    db: AsyncSession,
    *,
    phone: str,
) -> dict:
    from models.appointment import Appointment

    phone_norm = phone.strip().replace(" ", "").replace("-", "")
    if not phone_norm.startswith("+"):
        return {"found": False, "error": f"phone {phone!r} not in E.164"}

    pat = (await db.execute(
        select(Patient).where(Patient.phone_e164 == phone_norm)
    )).scalar_one_or_none()
    if pat is None:
        return {"found": False, "reason": "No patient found for that phone."}

    appt = (await db.execute(
        select(Appointment)
        .where(
            Appointment.patient_id == pat.id,
            Appointment.status.in_(("scheduled", "confirmed")),
        )
        .order_by(Appointment.slot_start_utc.asc())
        .limit(1)
    )).scalar_one_or_none()
    if appt is None:
        return {"found": False, "reason": "No active appointments for that patient."}

    return {
        "found": True,
        "appointment_id": appt.id,
        "patient_id": pat.id,
        "doctor_id": appt.doctor_id,
        "slot_start_utc": appt.slot_start_utc.isoformat(),
        "slot_pkt": (appt.slot_start_utc + _PKT_OFFSET).strftime("%Y-%m-%d %H:%M"),
        "status": appt.status,
        "type": appt.appointment_type,
    }


async def _tool_reschedule_appointment(
    db: AsyncSession,
    *,
    appointment_id: int,
    date_pkt: str,
    time_pkt: str,
) -> dict:
    from models.appointment import Appointment

    try:
        d = date_cls.fromisoformat(date_pkt)
        hh, mm = (int(p) for p in time_pkt.strip().split(":")[:2])
    except Exception:
        return {"error": f"invalid date/time: {date_pkt!r} {time_pkt!r}"}

    appt = (await db.execute(
        select(Appointment).where(Appointment.id == appointment_id)
    )).scalar_one_or_none()
    if appt is None:
        return {"error": f"appointment {appointment_id} not found"}
    if appt.status in ("cancelled", "completed"):
        return {"error": f"cannot reschedule appointment with status={appt.status}"}

    new_pkt_naive = datetime(d.year, d.month, d.day, hh, mm, 0)
    new_utc = (new_pkt_naive - _PKT_OFFSET).replace(tzinfo=timezone.utc)

    # Conflict check on the doctor.
    conflict = (await db.execute(
        select(Appointment.id).where(
            Appointment.doctor_id == appt.doctor_id,
            Appointment.slot_start_utc == new_utc,
            Appointment.status != "cancelled",
            Appointment.id != appt.id,
        )
    )).scalar_one_or_none()
    if conflict is not None:
        return {"error": "the requested slot is already booked for this doctor"}

    duration = (appt.slot_end_utc - appt.slot_start_utc)
    appt.slot_start_utc = new_utc
    appt.slot_end_utc = new_utc + duration
    appt.status = "rescheduled"
    await db.commit()
    return {
        "appointment_id": appt.id,
        "status": appt.status,
        "new_slot_pkt": f"{date_pkt} {time_pkt}",
        "new_slot_start_utc": appt.slot_start_utc.isoformat(),
    }


async def _tool_cancel_appointment(
    db: AsyncSession,
    *,
    appointment_id: int,
    reason: Optional[str] = None,
) -> dict:
    from models.appointment import Appointment

    appt = (await db.execute(
        select(Appointment).where(Appointment.id == appointment_id)
    )).scalar_one_or_none()
    if appt is None:
        return {"error": f"appointment {appointment_id} not found"}
    if appt.status == "cancelled":
        return {"error": "appointment is already cancelled"}

    appt.status = "cancelled"
    appt.cancelled_at = datetime.now(timezone.utc)
    appt.cancelled_by = "patient"
    if reason:
        appt.notes = (appt.notes + " | " if appt.notes else "") + f"cancel-reason: {reason}"
    await db.commit()
    return {"appointment_id": appt.id, "status": "cancelled"}


_TOOL_DISPATCH = {
    "list_doctors": _tool_list_doctors,
    "search_doctors": _tool_search_doctors,
    "find_available_slots": _tool_find_available_slots,
    "find_earliest_slot": _tool_find_earliest_slot,
    "triage_severity": _tool_triage_severity,
    "find_or_create_patient": _tool_find_or_create_patient,
    "find_existing_appointment": _tool_find_existing_appointment,
    "book_appointment": _tool_book_appointment,
    "reschedule_appointment": _tool_reschedule_appointment,
    "cancel_appointment_tool": _tool_cancel_appointment,
}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class StartRequest(BaseModel):
    language: LanguageCode = "en"


class StartResponse(BaseModel):
    session_id: str
    language: LanguageCode
    reply_text: str
    reply_audio_b64: Optional[str] = None
    tts_error: Optional[str] = None


class MessageRequest(BaseModel):
    session_id: str
    language: LanguageCode = "en"
    text: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list)


class ToolCallTrace(BaseModel):
    name: str
    arguments: dict
    result: dict


class MessageResponse(BaseModel):
    reply_text: str
    reply_audio_b64: Optional[str] = None
    tts_error: Optional[str] = None
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class TranscribeResponse(BaseModel):
    text: str


class EndRequest(BaseModel):
    session_id: str


# ---------------------------------------------------------------------------
# OpenAI helpers
# ---------------------------------------------------------------------------

def _client() -> AsyncOpenAI:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPENAI_API_KEY is not set in the server environment",
        )
    return AsyncOpenAI(api_key=api_key)


async def _synthesize(text: str, voice: str) -> tuple[Optional[str], Optional[str]]:
    """Return (base64_mp3, error_message). Never raises."""
    try:
        client = _client()
        resp = await client.audio.speech.create(
            model="tts-1",
            voice=voice,  # type: ignore[arg-type]
            input=text,
            response_format="mp3",
        )
        return base64.b64encode(resp.content).decode("ascii"), None
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("test-session TTS failed: %s", e)
        return None, str(e)


class _ChatRunResult:
    __slots__ = ("reply_text", "traces", "llm_ms", "input_tokens", "output_tokens")

    def __init__(self) -> None:
        self.reply_text: str = ""
        self.traces: list[ToolCallTrace] = []
        self.llm_ms: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0


async def _chat_with_tools(
    db: AsyncSession,
    *,
    language: LanguageCode,
    history: list[ChatTurn],
    user_text: str,
    session_id: str,
) -> _ChatRunResult:
    """Run an OpenAI chat completion with tool calling until the model produces
    a final user-facing reply or we hit the iteration cap. Tracks latency and
    token usage across all iterations."""
    client = _client()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(language)},
    ]
    for turn in history[-12:]:
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": user_text})

    out = _ChatRunResult()
    MAX_ITERS = 6
    t_start = time.monotonic()

    for _ in range(MAX_ITERS):
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=_TOOLS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=400,
        )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            out.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
            out.output_tokens += getattr(usage, "completion_tokens", 0) or 0
        msg = resp.choices[0].message

        if not msg.tool_calls:
            out.reply_text = (msg.content or "").strip()
            out.llm_ms = int((time.monotonic() - t_start) * 1000)
            return out

        messages.append({
            "role": "assistant",
            "content": msg.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            handler = _TOOL_DISPATCH.get(name)
            if handler is None:
                result: dict = {"error": f"unknown tool {name!r}"}
            else:
                try:
                    if name == "book_appointment":
                        result = await handler(db, session_id=session_id, **args)
                    else:
                        result = await handler(db, **args)
                except Exception as e:
                    logger.exception("test-session tool %s failed", name)
                    result = {"error": str(e)}

            out.traces.append(ToolCallTrace(name=name, arguments=args, result=result))
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            })

    # Iteration cap — final answer with tools disabled.
    final = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.3,
        max_tokens=200,
    )
    usage = getattr(final, "usage", None)
    if usage is not None:
        out.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
        out.output_tokens += getattr(usage, "completion_tokens", 0) or 0
    out.reply_text = (final.choices[0].message.content or "").strip()
    out.llm_ms = int((time.monotonic() - t_start) * 1000)
    return out


# ---------------------------------------------------------------------------
# Call logging helpers — write CallLog + Transcript rows so the call detail
# page populates with real metrics and a real transcript.
# ---------------------------------------------------------------------------

async def _ensure_call_log(
    db: AsyncSession, *, session_id: str, language: str
) -> CallLog:
    res = await db.execute(select(CallLog).where(CallLog.session_id == session_id))
    log = res.scalar_one_or_none()
    if log is not None:
        return log

    log = CallLog(
        session_id=session_id,
        language=language,
        outcome="answered",
        telephony_provider="browser-test",
        stt_provider="openai-whisper-1",
        llm_provider="openai",
        tts_provider="openai-tts-1",
        llm_model="gpt-4o-mini",
        started_at=datetime.now(timezone.utc),
        turn_count=0,
        avg_stt_ms=0,
        avg_llm_ms=0,
        avg_tts_ms=0,
        avg_total_ms=0,
        cost_stt_usd=0.0,
        cost_llm_usd=0.0,
        cost_tts_usd=0.0,
        cost_telephony_usd=0.0,
        cost_total_usd=0.0,
    )
    db.add(log)
    await db.flush()
    return log


def _running_avg(prev_avg: Optional[int], n: int, new_val: int) -> int:
    """Welford-style incremental mean (rounded to int ms)."""
    prev_avg = prev_avg or 0
    if n <= 1:
        return new_val
    return int((prev_avg * (n - 1) + new_val) / n)


async def _record_turn(
    db: AsyncSession,
    *,
    log: CallLog,
    user_text: str,
    assistant_text: str,
    language: str,
    llm_ms: int,
    tts_ms: int,
    stt_ms: int,
    llm_input_tokens: int,
    llm_output_tokens: int,
    stt_audio_seconds: float,
    traces: list[ToolCallTrace],
) -> None:
    is_rtl = language in ("ur-PK", "pa-PK")
    log.turn_count = (log.turn_count or 0) + 1
    next_user_turn = log.turn_count * 2 - 1
    next_asst_turn = log.turn_count * 2

    db.add(Transcript(
        call_log_id=log.id,
        session_id=log.session_id,
        turn_id=next_user_turn,
        speaker="patient",
        language=language,
        is_rtl=is_rtl,
        raw_text=user_text,
        masked_text=user_text,
        stt_provider=log.stt_provider if stt_ms > 0 else None,
    ))
    db.add(Transcript(
        call_log_id=log.id,
        session_id=log.session_id,
        turn_id=next_asst_turn,
        speaker="assistant",
        language=language,
        is_rtl=is_rtl,
        raw_text=assistant_text,
        masked_text=assistant_text,
    ))

    n = log.turn_count
    log.avg_llm_ms = _running_avg(log.avg_llm_ms, n, llm_ms)
    log.avg_tts_ms = _running_avg(log.avg_tts_ms, n, tts_ms)
    if stt_ms > 0:
        log.avg_stt_ms = _running_avg(log.avg_stt_ms, n, stt_ms)
    log.avg_total_ms = _running_avg(log.avg_total_ms, n, stt_ms + llm_ms + tts_ms)

    # Cost accumulation
    log.cost_llm_usd = (log.cost_llm_usd or 0.0) + (
        llm_input_tokens * _LLM_INPUT_USD_PER_TOKEN
        + llm_output_tokens * _LLM_OUTPUT_USD_PER_TOKEN
    )
    log.cost_tts_usd = (log.cost_tts_usd or 0.0) + len(assistant_text) * _TTS_USD_PER_CHAR
    if stt_audio_seconds > 0:
        log.cost_stt_usd = (log.cost_stt_usd or 0.0) + (stt_audio_seconds / 60.0) * _WHISPER_USD_PER_MINUTE
    log.cost_total_usd = (
        (log.cost_stt_usd or 0.0)
        + (log.cost_llm_usd or 0.0)
        + (log.cost_tts_usd or 0.0)
        + (log.cost_telephony_usd or 0.0)
    )

    # Outcome detection from tool traces
    for tr in traces:
        if tr.name == "book_appointment" and tr.result.get("appointment_id"):
            log.appointment_id = tr.result["appointment_id"]
            log.outcome = "booked"
        if tr.name == "find_or_create_patient" and tr.result.get("patient_id"):
            log.patient_id = tr.result["patient_id"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/start", response_model=StartResponse)
async def start_session(
    body: StartRequest,
    user: Annotated[User, Depends(require_any_staff)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StartResponse:
    cfg = _LANG_CONFIG[body.language]
    session_id = str(uuid.uuid4())  # 36 chars — fits CallLog.session_id

    log = await _ensure_call_log(db, session_id=session_id, language=body.language)

    t0 = time.monotonic()
    audio_b64, tts_err = await _synthesize(cfg["greeting"], cfg["voice"])
    tts_ms = int((time.monotonic() - t0) * 1000)

    # Greeting is the first assistant turn (no user input yet).
    log.turn_count = (log.turn_count or 0) + 1
    db.add(Transcript(
        call_log_id=log.id,
        session_id=log.session_id,
        turn_id=1,
        speaker="assistant",
        language=body.language,
        is_rtl=body.language in ("ur-PK", "pa-PK"),
        raw_text=cfg["greeting"],
        masked_text=cfg["greeting"],
    ))
    log.avg_tts_ms = tts_ms
    log.avg_total_ms = tts_ms
    log.cost_tts_usd = (log.cost_tts_usd or 0.0) + len(cfg["greeting"]) * _TTS_USD_PER_CHAR
    log.cost_total_usd = (
        (log.cost_stt_usd or 0.0) + (log.cost_llm_usd or 0.0)
        + (log.cost_tts_usd or 0.0) + (log.cost_telephony_usd or 0.0)
    )
    await db.commit()

    ws_manager.broadcast_fire_and_forget({
        "type": "call.started",
        "session_id": session_id,
        "phone": "browser-test",
        "language": body.language,
        "test": True,
    })

    return StartResponse(
        session_id=session_id,
        language=body.language,
        reply_text=cfg["greeting"],
        reply_audio_b64=audio_b64,
        tts_error=tts_err,
    )


@router.post("/message", response_model=MessageResponse)
async def send_message(
    body: MessageRequest,
    user: Annotated[User, Depends(require_any_staff)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MessageResponse:
    cfg = _LANG_CONFIG[body.language]

    log = await _ensure_call_log(db, session_id=body.session_id, language=body.language)

    try:
        run = await _chat_with_tools(
            db,
            language=body.language,
            history=body.history,
            user_text=body.text,
            session_id=body.session_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("test-session chat failed")
        raise HTTPException(status_code=502, detail=f"LLM error: {e}")

    t_tts = time.monotonic()
    audio_b64, tts_err = await _synthesize(run.reply_text, cfg["voice"])
    tts_ms = int((time.monotonic() - t_tts) * 1000)

    await _record_turn(
        db,
        log=log,
        user_text=body.text,
        assistant_text=run.reply_text,
        language=body.language,
        llm_ms=run.llm_ms,
        tts_ms=tts_ms,
        stt_ms=0,
        llm_input_tokens=run.input_tokens,
        llm_output_tokens=run.output_tokens,
        stt_audio_seconds=0.0,
        traces=run.traces,
    )
    await db.commit()

    return MessageResponse(
        reply_text=run.reply_text,
        reply_audio_b64=audio_b64,
        tts_error=tts_err,
        tool_calls=run.traces,
    )


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    user: Annotated[User, Depends(require_any_staff)],
    audio: UploadFile = File(...),
    language: LanguageCode = Form("en"),
) -> TranscribeResponse:
    cfg = _LANG_CONFIG[language]
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty audio upload")

    client = _client()
    try:
        resp = await client.audio.transcriptions.create(
            model="whisper-1",
            file=(audio.filename or "audio.webm", raw, audio.content_type or "audio/webm"),
            language=cfg["whisper_lang"],
        )
    except Exception as e:
        logger.exception("test-session transcribe failed")
        raise HTTPException(status_code=502, detail=f"STT error: {e}")

    return TranscribeResponse(text=(resp.text or "").strip())


@router.post("/end", status_code=204)
async def end_session(
    body: EndRequest,
    user: Annotated[User, Depends(require_any_staff)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    res = await db.execute(select(CallLog).where(CallLog.session_id == body.session_id))
    log = res.scalar_one_or_none()
    if log is not None:
        log.ended_at = datetime.now(timezone.utc)
        if log.started_at:
            started = log.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            log.duration_seconds = max(0, int((log.ended_at - started).total_seconds()))
        await db.commit()

    ws_manager.broadcast_fire_and_forget({
        "type": "call.ended",
        "session_id": body.session_id,
        "test": True,
    })
