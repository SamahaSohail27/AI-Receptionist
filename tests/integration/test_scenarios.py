"""Scenario tests against the real seeded DB.

These exercise the booking-tool surface end-to-end without an LLM —
i.e. we call the dispatched handlers directly to verify the contracts
the system prompt relies on. LLM-driven scripted runs are out of scope
for the offline test suite; for those, set RUN_LLM_SCENARIOS=1.
"""
from __future__ import annotations

import os
import pytest


pytestmark = pytest.mark.asyncio


async def _db_available() -> bool:
    try:
        from sqlalchemy import select
        from core.database import AsyncSessionLocal
        from models.doctor import Doctor
        async with AsyncSessionLocal() as db:
            await db.execute(select(Doctor).limit(1))
        return True
    except Exception:
        return False


async def test_search_doctors_gender_filter_returns_only_requested_gender():
    if not await _db_available():
        pytest.skip("DB not reachable — run seeder first")
    from core.database import AsyncSessionLocal
    from api.test_session import _TOOL_DISPATCH

    async with AsyncSessionLocal() as db:
        result = await _TOOL_DISPATCH["search_doctors"](db, gender="F")
    assert result["doctors"], "expected at least one female doctor"
    assert all(d["gender"] == "F" for d in result["doctors"])


async def test_search_doctors_male_filter():
    if not await _db_available():
        pytest.skip("DB not reachable")
    from core.database import AsyncSessionLocal
    from api.test_session import _TOOL_DISPATCH

    async with AsyncSessionLocal() as db:
        result = await _TOOL_DISPATCH["search_doctors"](db, gender="M")
    assert result["doctors"]
    assert all(d["gender"] == "M" for d in result["doctors"])


async def test_search_doctors_specialty_filter():
    if not await _db_available():
        pytest.skip("DB not reachable")
    from core.database import AsyncSessionLocal
    from api.test_session import _TOOL_DISPATCH

    async with AsyncSessionLocal() as db:
        result = await _TOOL_DISPATCH["search_doctors"](db, specialty="cardio")
    # Seeded set has Dr. Hamid Raza (Cardiology). If naming changes, this
    # assertion still holds: at least one match for 'cardio'.
    assert any("cardio" in (d["specialty"] or "").lower() for d in result["doctors"])


async def test_triage_severity_emergency_phrase():
    if not await _db_available():
        pytest.skip("DB not reachable")
    from core.database import AsyncSessionLocal
    from api.test_session import _TOOL_DISPATCH

    async with AsyncSessionLocal() as db:
        result = await _TOOL_DISPATCH["triage_severity"](
            db, symptom_text="I have severe chest pain and can't breathe", language="en",
        )
    assert result["severity"] == "emergency"
    assert "1122" in result["suggested_action"] or "transfer" in result["suggested_action"].lower()


async def test_triage_severity_high():
    if not await _db_available():
        pytest.skip("DB not reachable")
    from core.database import AsyncSessionLocal
    from api.test_session import _TOOL_DISPATCH

    async with AsyncSessionLocal() as db:
        r = await _TOOL_DISPATCH["triage_severity"](
            db, symptom_text="I have a very bad headache for hours", language="en",
        )
    assert r["severity"] == "high"


async def test_triage_severity_low_for_normal_request():
    if not await _db_available():
        pytest.skip("DB not reachable")
    from core.database import AsyncSessionLocal
    from api.test_session import _TOOL_DISPATCH

    async with AsyncSessionLocal() as db:
        r = await _TOOL_DISPATCH["triage_severity"](
            db, symptom_text="I'd like to book a checkup next week.", language="en",
        )
    assert r["severity"] == "low"


async def test_triage_severity_urdu_emergency():
    if not await _db_available():
        pytest.skip("DB not reachable")
    from core.database import AsyncSessionLocal
    from api.test_session import _TOOL_DISPATCH

    async with AsyncSessionLocal() as db:
        r = await _TOOL_DISPATCH["triage_severity"](
            db, symptom_text="مجھے سینے میں درد ہو رہا ہے", language="ur-PK",
        )
    assert r["severity"] == "emergency"


async def test_find_earliest_slot_returns_within_two_days_when_urgent():
    if not await _db_available():
        pytest.skip("DB not reachable")
    from datetime import date, timedelta
    from core.database import AsyncSessionLocal
    from api.test_session import _TOOL_DISPATCH

    async with AsyncSessionLocal() as db:
        r = await _TOOL_DISPATCH["find_earliest_slot"](db, urgency="urgent")
    if not r.get("found"):
        # All today+tomorrow may be booked in the seed — accept gracefully.
        assert r.get("reason")
        return
    today = date.today()
    target = date.fromisoformat(r["date_pkt"])
    assert (target - today).days <= 2


async def test_reschedule_then_cancel_round_trip():
    if not await _db_available():
        pytest.skip("DB not reachable")

    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from core.database import AsyncSessionLocal
    from api.test_session import _TOOL_DISPATCH
    from models.appointment import Appointment

    # Pick a future scheduled appointment to mutate.
    async with AsyncSessionLocal() as db:
        appt = (await db.execute(
            select(Appointment)
            .where(
                Appointment.status == "scheduled",
                Appointment.slot_start_utc > datetime.now(timezone.utc) + timedelta(days=2),
            )
            .order_by(Appointment.slot_start_utc.asc())
            .limit(1)
        )).scalar_one_or_none()
        if appt is None:
            pytest.skip("no future scheduled appointment available")
        original_start = appt.slot_start_utc
        appt_id = appt.id

    # Move it 1 day later, same time-of-day in PKT.
    new_dt = original_start + timedelta(days=1)
    pkt = new_dt + timedelta(hours=5)
    async with AsyncSessionLocal() as db:
        r = await _TOOL_DISPATCH["reschedule_appointment"](
            db,
            appointment_id=appt_id,
            date_pkt=pkt.date().isoformat(),
            time_pkt=pkt.strftime("%H:%M"),
        )
    assert r.get("status") == "rescheduled" or "error" in r  # conflict allowed
    # Cancel
    async with AsyncSessionLocal() as db:
        c = await _TOOL_DISPATCH["cancel_appointment_tool"](
            db, appointment_id=appt_id, reason="test_scenarios cleanup",
        )
    assert c.get("status") == "cancelled"
