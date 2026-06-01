"""
Seed dummy data for local/dev: doctors + availability + patients + appointments + call logs.

Usage (from project root):
    eval "$(conda shell.bash hook 2>/dev/null)" && conda activate ai-receptionist
    python scripts/seed_dummy_data.py             # add data (idempotent on phone)
    python scripts/seed_dummy_data.py --reset     # wipe seeded tables first

Idempotency:
    Doctors are matched by name_en, patients by phone_e164. Re-running won't
    create duplicates of those. Appointments are regenerated each run only
    after --reset (otherwise existing slots may collide with the unique
    (doctor_id, slot_start_utc) constraint and be skipped).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import delete, select  # noqa: E402

from core.database import AsyncSessionLocal  # noqa: E402
from models.appointment import Appointment, SlotReservation  # noqa: E402
from models.audit import NotificationLog  # noqa: E402
from models.call_log import CallLog, Transcript  # noqa: E402
from models.doctor import Doctor, DoctorAvailability  # noqa: E402
from models.patient import Patient  # noqa: E402

PKT = timezone(timedelta(hours=5))


DOCTORS = [
    {
        "name_en": "Dr. Asma Khan",
        "name_ur": "ڈاکٹر اسما خان",
        "speciality": "General Physician",
        "speciality_ur": "جنرل فزیشن",
        "department": "OPD",
        "room_number": "101",
        "consultation_fee": 1500,
        "consultation_duration_minutes": 20,
        "preferred_language": "ur-PK",
        "calendar_color": "#0EA5E9",
        "insurance_panels": ["sehat_sahulat", "jubilee"],
        "gender": "F",
    },
    {
        "name_en": "Dr. Hamid Raza",
        "name_ur": "ڈاکٹر حامد رضا",
        "speciality": "Cardiology",
        "speciality_ur": "امراضِ قلب",
        "department": "Cardiology",
        "room_number": "204",
        "consultation_fee": 3500,
        "consultation_duration_minutes": 30,
        "preferred_language": "ur-PK",
        "calendar_color": "#EF4444",
        "insurance_panels": ["sehat_sahulat", "igloo", "jubilee"],
        "gender": "M",
    },
    {
        "name_en": "Dr. Sana Iqbal",
        "name_ur": "ڈاکٹر ثناء اقبال",
        "speciality": "Pediatrics",
        "speciality_ur": "اطفال",
        "department": "Pediatrics",
        "room_number": "115",
        "consultation_fee": 2000,
        "consultation_duration_minutes": 20,
        "preferred_language": "ur-PK",
        "calendar_color": "#F59E0B",
        "insurance_panels": ["sehat_sahulat"],
        "gender": "F",
    },
    {
        "name_en": "Dr. Bilal Ahmed",
        "name_ur": "ڈاکٹر بلال احمد",
        "speciality": "Orthopedics",
        "speciality_ur": "ہڈیوں کے امراض",
        "department": "Orthopedics",
        "room_number": "302",
        "consultation_fee": 3000,
        "consultation_duration_minutes": 25,
        "preferred_language": "ur-PK",
        "calendar_color": "#10B981",
        "insurance_panels": ["igloo"],
        "gender": "M",
    },
    {
        "name_en": "Dr. Mehwish Ali",
        "name_ur": "ڈاکٹر مہوش علی",
        "speciality": "Gynaecology",
        "speciality_ur": "زنانہ امراض",
        "department": "Gynae",
        "room_number": "210",
        "consultation_fee": 2500,
        "consultation_duration_minutes": 25,
        "preferred_language": "ur-PK",
        "calendar_color": "#A855F7",
        "insurance_panels": ["sehat_sahulat", "jubilee"],
        "gender": "F",
    },
    {
        "name_en": "Dr. Usman Tariq",
        "name_ur": "ڈاکٹر عثمان طارق",
        "speciality": "ENT",
        "speciality_ur": "ناک کان گلا",
        "department": "ENT",
        "room_number": "108",
        "consultation_fee": 1800,
        "consultation_duration_minutes": 20,
        "preferred_language": "pa-PK",
        "calendar_color": "#14B8A6",
        "insurance_panels": [],
        "gender": "M",
    },
]


PATIENTS = [
    ("Ayesha Siddiqui", "عائشہ صدیقی", "+923001234001", 1992, "F", "ur-PK"),
    ("Muhammad Imran", "محمد عمران", "+923001234002", 1985, "M", "ur-PK"),
    ("Fatima Noor", "فاطمہ نور", "+923001234003", 1978, "F", "ur-PK"),
    ("Hassan Raza", "حسن رضا", "+923001234004", 1995, "M", "pa-PK"),
    ("Zainab Bibi", "زینب بی بی", "+923001234005", 1965, "F", "ur-PK"),
    ("Ali Hamza", "علی حمزہ", "+923001234006", 2001, "M", "en"),
    ("Sara Malik", "سارہ ملک", "+923001234007", 1989, "F", "ur-PK"),
    ("Tariq Mehmood", "طارق محمود", "+923001234008", 1972, "M", "pa-PK"),
    ("Rabia Khan", "ربیعہ خان", "+923001234009", 1998, "F", "ur-PK"),
    ("Shahbaz Ahmed", "شہباز احمد", "+923001234010", 1980, "M", "ur-PK"),
    ("Nida Aslam", "ندا اسلم", "+923001234011", 1993, "F", "en"),
    ("Faisal Javed", "فیصل جاوید", "+923001234012", 1968, "M", "ur-PK"),
    ("Iqra Hussain", "اقرا حسین", "+923001234013", 2000, "F", "ur-PK"),
    ("Kamran Sheikh", "کامران شیخ", "+923001234014", 1976, "M", "pa-PK"),
    ("Maryam Yousaf", "مریم یوسف", "+923001234015", 1990, "F", "ur-PK"),
]


def _cnic_hash(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


async def reset_seeded_tables(db) -> None:
    print("Wiping appointments, slot_reservations, transcripts, call_logs, notifications…")
    await db.execute(delete(NotificationLog))
    await db.execute(delete(Transcript))
    await db.execute(delete(CallLog))
    await db.execute(delete(SlotReservation))
    await db.execute(delete(Appointment))
    await db.execute(delete(DoctorAvailability))
    await db.execute(delete(Patient))
    await db.execute(delete(Doctor))
    await db.commit()


async def seed_doctors(db) -> list[Doctor]:
    existing = (await db.execute(select(Doctor))).scalars().all()
    by_name = {d.name_en: d for d in existing}
    created: list[Doctor] = []
    for spec in DOCTORS:
        if spec["name_en"] in by_name:
            created.append(by_name[spec["name_en"]])
            continue
        d = Doctor(**spec, is_active=True)
        db.add(d)
        created.append(d)
    await db.flush()
    print(f"Doctors: {len(created)} (created {len(created) - len(existing)})")
    return created


async def seed_availability(db, doctors: list[Doctor]) -> None:
    existing_ids = {
        (a.doctor_id, a.day_of_week)
        for a in (await db.execute(select(DoctorAvailability))).scalars().all()
    }
    new_count = 0
    for doc in doctors:
        for day in range(0, 5):  # Mon–Fri
            if (doc.id, day) in existing_ids:
                continue
            db.add(DoctorAvailability(
                doctor_id=doc.id,
                day_of_week=day,
                start_time="09:00",
                end_time="17:00",
                is_active=True,
            ))
            new_count += 1
        if (doc.id, 5) not in existing_ids:
            db.add(DoctorAvailability(
                doctor_id=doc.id,
                day_of_week=5,  # Saturday half-day
                start_time="09:00",
                end_time="13:00",
                is_active=True,
            ))
            new_count += 1
    await db.flush()
    print(f"Availability slots created: {new_count}")


async def seed_patients(db) -> list[Patient]:
    existing = (await db.execute(select(Patient))).scalars().all()
    by_phone = {p.phone_e164: p for p in existing}
    created: list[Patient] = []
    for name_en, name_ur, phone, birth, gender, lang in PATIENTS:
        if phone in by_phone:
            created.append(by_phone[phone])
            continue
        p = Patient(
            cnic_hash=_cnic_hash(phone),
            phone_e164=phone,
            name_en=name_en,
            name_ur=name_ur,
            birth_year=birth,
            gender=gender,
            preferred_language=lang,
            whatsapp_opted_in=random.random() < 0.6,
        )
        db.add(p)
        created.append(p)
    await db.flush()
    print(f"Patients: {len(created)} (created {len(created) - len(existing)})")
    return created


async def seed_appointments(
    db, doctors: list[Doctor], patients: list[Patient]
) -> list[Appointment]:
    """
    Spread ~80 appointments from 14 days ago to 21 days ahead.
    Each doctor has 2-4 slots per weekday; statuses skewed by date (past = completed/no_show).
    """
    rng = random.Random(42)
    types = ["consultation", "follow_up", "checkup", "emergency", "procedure"]
    type_weights = [0.55, 0.25, 0.10, 0.03, 0.07]
    sources = ["ai", "manual", "walk_in"]
    source_weights = [0.55, 0.35, 0.10]

    # Build candidate slot times in PKT (clinic hours 09:00–17:00 in 30-min steps)
    slot_minutes = list(range(0, (17 - 9) * 60, 30))

    today_pkt_date = (datetime.now(timezone.utc) + timedelta(hours=5)).date()
    created: list[Appointment] = []
    skipped_conflict = 0

    # Avoid colliding with already-existing slots
    existing_keys = {
        (a.doctor_id, a.slot_start_utc.replace(microsecond=0))
        for a in (await db.execute(select(Appointment))).scalars().all()
    }

    for day_offset in range(-14, 22):
        target_date = today_pkt_date + timedelta(days=day_offset)
        if target_date.weekday() == 6:  # skip Sunday
            continue
        for doc in doctors:
            num_appts = rng.randint(2, 4)
            chosen_minutes = rng.sample(slot_minutes, k=num_appts)
            for minute_offset in chosen_minutes:
                hour = 9 + minute_offset // 60
                minute = minute_offset % 60
                slot_pkt = datetime(
                    target_date.year, target_date.month, target_date.day,
                    hour, minute, tzinfo=PKT,
                )
                slot_utc = slot_pkt.astimezone(timezone.utc).replace(microsecond=0)
                key = (doc.id, slot_utc)
                if key in existing_keys:
                    skipped_conflict += 1
                    continue
                existing_keys.add(key)

                patient = rng.choice(patients)
                appt_type = rng.choices(types, weights=type_weights, k=1)[0]
                source = rng.choices(sources, weights=source_weights, k=1)[0]

                if day_offset < -1:
                    status = rng.choices(
                        ["completed", "no_show", "cancelled"],
                        weights=[0.80, 0.12, 0.08],
                    )[0]
                elif day_offset == 0:
                    status = rng.choices(
                        ["confirmed", "scheduled", "completed", "cancelled"],
                        weights=[0.45, 0.30, 0.20, 0.05],
                    )[0]
                else:
                    status = rng.choices(
                        ["scheduled", "confirmed", "cancelled"],
                        weights=[0.70, 0.25, 0.05],
                    )[0]

                cancelled_at = None
                cancelled_by = None
                if status == "cancelled":
                    cancelled_at = slot_utc - timedelta(hours=rng.randint(1, 48))
                    cancelled_by = rng.choice(["patient", "staff", "system"])

                appt = Appointment(
                    patient_id=patient.id,
                    doctor_id=doc.id,
                    slot_start_utc=slot_utc,
                    slot_end_utc=slot_utc + timedelta(minutes=doc.consultation_duration_minutes),
                    status=status,
                    appointment_type=appt_type,
                    booking_source=source,
                    notes=None,
                    idempotency_key=str(uuid.uuid4()),
                    cancelled_at=cancelled_at,
                    cancelled_by=cancelled_by,
                    reminder_24h_sent=day_offset >= 0 and rng.random() < 0.7,
                    reminder_2h_sent=day_offset == 0 and rng.random() < 0.5,
                    whatsapp_confirmation_sent=source == "ai" and rng.random() < 0.6,
                )
                db.add(appt)
                created.append(appt)

    await db.flush()
    print(f"Appointments created: {len(created)} (skipped {skipped_conflict} duplicate slots)")
    return created


_DIALOGUE_TEMPLATES = {
    "ur-PK": [
        ("patient", "السلام علیکم، میں اپائنٹمنٹ بک کرنا چاہتا ہوں۔"),
        ("assistant", "وعلیکم السلام! ضرور، آپ کس ڈاکٹر سے ملنا چاہتے ہیں؟"),
        ("patient", "ڈاکٹر اسما خان سے، کل صبح کا کوئی وقت۔"),
        ("assistant", "ٹھیک ہے، کل صبح ساڑھے دس بجے کا سلاٹ خالی ہے۔ کیا یہ مناسب ہوگا؟"),
        ("patient", "جی ہاں، ٹھیک ہے۔"),
        ("assistant", "بہت خوب۔ آپ کا اپائنٹمنٹ کنفرم ہو گیا ہے۔ تصدیق وٹس ایپ پر بھیج دی گئی ہے۔"),
        ("patient", "شکریہ۔"),
        ("assistant", "آپ کا دن اچھا گزرے۔ خدا حافظ۔"),
    ],
    "pa-PK": [
        ("patient", "ست سری اکال جی، مینوں ڈاکٹر نال ٹائم چاہیدا اے۔"),
        ("assistant", "جی بالکل، تواڈا کیہڑا ڈاکٹر پسند اے؟"),
        ("patient", "ڈاکٹر بلال احمد۔"),
        ("assistant", "اوہناں دا اج گیارہ وجے دا سلاٹ خالی اے۔"),
        ("patient", "ٹھیک اے، بک کر دیو۔"),
        ("assistant", "اپائنٹمنٹ پکی ہو گئی۔ شکریہ۔"),
    ],
    "en": [
        ("patient", "Hi, I'd like to book an appointment with a cardiologist."),
        ("assistant", "Of course. We have Dr. Hamid Raza available. Would you prefer morning or afternoon?"),
        ("patient", "Morning, please. Tomorrow if possible."),
        ("assistant", "Tomorrow at 10:30 AM is available. Shall I confirm?"),
        ("patient", "Yes, please confirm it."),
        ("assistant", "Confirmed. You'll receive a WhatsApp confirmation shortly. Anything else?"),
        ("patient", "No, that's all. Thank you."),
        ("assistant", "You're welcome. Have a good day."),
    ],
}


async def seed_transcripts(db, calls: list[CallLog]) -> int:
    """Insert turn-level transcript rows for each call (idempotent on call_log_id)."""
    rng = random.Random(11)
    existing_ids = {
        cid for (cid,) in (await db.execute(select(Transcript.call_log_id).distinct())).all()
    }
    written = 0
    for call in calls:
        if call.id in existing_ids:
            continue
        template = _DIALOGUE_TEMPLATES.get(call.language) or _DIALOGUE_TEMPLATES["en"]
        is_rtl = call.language in ("ur-PK", "pa-PK")
        # Use up to call.turn_count turns, repeating template if needed.
        n = max(2, min(call.turn_count or len(template), len(template) * 2))
        for i in range(n):
            speaker, text = template[i % len(template)]
            db.add(Transcript(
                call_log_id=call.id,
                session_id=call.session_id,
                turn_id=i + 1,
                speaker=speaker,
                language=call.language,
                is_rtl=is_rtl,
                raw_text=text,
                masked_text=text,
                stt_confidence=round(rng.uniform(0.55, 0.95), 2) if speaker == "patient" else None,
                stt_provider=call.stt_provider if speaker == "patient" else None,
            ))
            written += 1
    await db.flush()
    print(f"Transcript turns created: {written}")
    return written


async def seed_call_logs(
    db, patients: list[Patient], appointments: list[Appointment]
) -> None:
    rng = random.Random(7)
    languages = ["ur-PK", "pa-PK", "en"]
    lang_weights = [0.65, 0.20, 0.15]
    intents = ["book_appointment", "reschedule", "cancel", "info_query", "emergency", "directions"]
    outcomes = ["booked", "answered", "cancelled", "rescheduled", "transferred", "abandoned"]
    outcome_weights = [0.45, 0.20, 0.10, 0.05, 0.10, 0.10]

    ai_appts = [a for a in appointments if a.booking_source == "ai"]
    rng.shuffle(ai_appts)

    rows = 60
    for i in range(rows):
        started = datetime.now(timezone.utc) - timedelta(
            days=rng.randint(0, 14),
            hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59),
        )
        duration = rng.randint(45, 240)
        ended = started + timedelta(seconds=duration)
        lang = rng.choices(languages, weights=lang_weights, k=1)[0]
        outcome = rng.choices(outcomes, weights=outcome_weights, k=1)[0]
        emergency = outcome == "transferred" and rng.random() < 0.4

        linked_appt_id = None
        if outcome == "booked" and ai_appts:
            linked_appt_id = ai_appts[i % len(ai_appts)].id

        patient = rng.choice(patients)
        turns = rng.randint(4, 14)
        avg_stt = rng.randint(180, 320)
        avg_llm = rng.randint(280, 600)
        avg_tts = rng.randint(140, 260)
        avg_total = avg_stt + avg_llm + avg_tts

        call = CallLog(
            session_id=str(uuid.uuid4()),
            patient_id=patient.id,
            caller_phone_hash=_cnic_hash(patient.phone_e164),
            language=lang,
            intent=rng.choice(intents),
            outcome=outcome,
            telephony_provider=rng.choice(["plivo", "twilio"]),
            stt_provider=rng.choices(["deepgram", "groq", "google"], weights=[0.7, 0.2, 0.1])[0],
            llm_provider=rng.choices(["openai", "anthropic"], weights=[0.8, 0.2])[0],
            tts_provider=rng.choices(["elevenlabs", "cartesia", "azure"], weights=[0.6, 0.2, 0.2])[0],
            llm_model=rng.choice(["gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet"]),
            started_at=started,
            ended_at=ended,
            duration_seconds=duration,
            turn_count=turns,
            avg_stt_ms=avg_stt,
            avg_llm_ms=avg_llm,
            avg_tts_ms=avg_tts,
            avg_total_ms=avg_total,
            p95_total_ms=int(avg_total * 1.35),
            tts_cache_hits=rng.randint(0, turns),
            tts_cache_misses=rng.randint(0, turns),
            cost_stt_usd=round(rng.uniform(0.002, 0.02), 6),
            cost_llm_usd=round(rng.uniform(0.003, 0.04), 6),
            cost_tts_usd=round(rng.uniform(0.005, 0.05), 6),
            cost_telephony_usd=round(rng.uniform(0.01, 0.08), 6),
            cost_total_usd=round(rng.uniform(0.03, 0.18), 6),
            escalation_triggered=outcome == "transferred",
            escalation_reason="low_confidence" if outcome == "transferred" else None,
            emergency_detected=emergency,
            appointment_id=linked_appt_id,
            turn_metrics=None,
        )
        db.add(call)
    await db.flush()
    print(f"Call logs created: {rows}")

    # Pull all call_logs (newly created plus any pre-existing) so transcript
    # seeding sees their assigned ids.
    all_calls = (await db.execute(select(CallLog))).scalars().all()
    await seed_transcripts(db, all_calls)


async def main(reset: bool) -> None:
    async with AsyncSessionLocal() as db:
        if reset:
            await reset_seeded_tables(db)

        doctors = await seed_doctors(db)
        await seed_availability(db, doctors)
        patients = await seed_patients(db)
        appointments = await seed_appointments(db, doctors, patients)
        await seed_call_logs(db, patients, appointments)
        await db.commit()

    print("\nSeed complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed dummy data for AI Receptionist")
    parser.add_argument("--reset", action="store_true",
                        help="Wipe seeded tables before inserting")
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset))
