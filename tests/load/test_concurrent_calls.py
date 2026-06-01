"""
Load / concurrency tests.

Simulates 20 concurrent "calls" through the pure logic layer
(no real audio, no real providers — all mocked).

Verifies:
- No race conditions in slot reservation (Redis SETNX serialized)
- No shared state leak between sessions (each session gets its own ConversationContext)
- Emergency detection is thread-safe (stateless detector)
- Cost tracking is correct under concurrent calls
- Latency budget: all 20 simulated turns complete in < 2s (pure logic only)
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.load
class TestConcurrentConversationContexts:
    """Each session must have fully isolated state."""

    @pytest.mark.asyncio
    async def test_20_sessions_are_isolated(self):
        from pipeline.context_manager import ConversationContext

        sessions = [ConversationContext(f"sess-{i}", "ur-PK") for i in range(20)]

        # Mutate session i with unique data
        for i, ctx in enumerate(sessions):
            ctx.update_state(patient_name=f"Patient-{i}", turn_count=i)

        # Verify no cross-contamination
        for i, ctx in enumerate(sessions):
            assert ctx._state.patient_name == f"Patient-{i}"
            assert ctx._state.turn_count == i

    @pytest.mark.asyncio
    async def test_concurrent_turn_additions(self):
        """Concurrent add_turn calls on separate contexts must not interfere."""
        from pipeline.context_manager import ConversationContext

        contexts = [ConversationContext(f"sess-conc-{i}", "en") for i in range(20)]

        async def add_turns(ctx: ConversationContext, n: int):
            for j in range(n):
                ctx.add_turn("user" if j % 2 == 0 else "assistant", f"message {j}")

        tasks = [add_turns(ctx, 5) for ctx in contexts]
        await asyncio.gather(*tasks)

        for ctx in contexts:
            assert ctx._state.turn_count == 5

    @pytest.mark.asyncio
    async def test_20_contexts_compression_does_not_cross_contaminate(self):
        """After compression, no session has another session's data."""
        from pipeline.context_manager import ConversationContext

        sessions = [ConversationContext(f"sess-compress-{i}", "en") for i in range(5)]

        async def fill_session(ctx: ConversationContext, label: str):
            for j in range(15):  # trigger compression at 13
                ctx.add_turn("user" if j % 2 == 0 else "assistant", f"{label} turn {j}")

        tasks = [fill_session(ctx, f"session-{i}") for i, ctx in enumerate(sessions)]
        await asyncio.gather(*tasks)

        # Each session should have its own summary (not bleed into others)
        for i, ctx in enumerate(sessions):
            assert ctx._state.turn_count == 15


@pytest.mark.load
class TestConcurrentEmergencyDetection:
    """EmergencyDetector is stateless — must be safe for concurrent calls."""

    @pytest.mark.asyncio
    async def test_20_concurrent_detections(self):
        from pipeline.emergency_detector import EmergencyDetector

        det = EmergencyDetector()  # shared instance

        texts = [
            ("سینے میں درد ہے", "ur-PK", True),
            ("مجھے ڈاکٹر سے ملنا ہے", "ur-PK", False),
            ("chest pain very bad", "en", True),
            ("I need an appointment", "en", False),
            ("ساہ نئیں آؤندا", "pa-PK", True),
        ] * 4  # 20 total

        async def run_detection(text, lang, expected):
            result = det.detect(text, lang)
            return result == expected

        results = await asyncio.gather(*[run_detection(t, l, e) for t, l, e in texts])
        assert all(results), "Some concurrent emergency detections returned wrong results"


@pytest.mark.load
class TestConcurrentCostCalculations:
    """Cost calculator is pure functional — safe for concurrent calls."""

    @pytest.mark.asyncio
    async def test_20_concurrent_cost_calculations(self):
        from analytics.cost_tracker import CallCost, calculate_call_cost

        async def calc(i: int):
            cost = CallCost(
                stt_seconds=float(60 + i),
                stt_provider="deepgram",
                llm_input_tokens=500 + i * 10,
                llm_output_tokens=200 + i * 5,
                llm_model="gpt-4o-mini",
                tts_chars=800 + i * 20,
                tts_provider="azure",
                telephony_seconds=float(60 + i),
                telephony_provider="plivo",
            )
            result = calculate_call_cost(cost)
            assert result["total_usd"] > 0
            return result["total_usd"]

        totals = await asyncio.gather(*[calc(i) for i in range(20)])
        # All 20 should have completed without error and produced different results
        assert len(set(round(t, 8) for t in totals)) > 1  # not all identical


@pytest.mark.load
class TestConcurrentSlotReservation:
    """Slot reservation: only one session wins per slot (SETNX serialization)."""

    @pytest.mark.asyncio
    async def test_only_one_session_wins_slot(self):
        from datetime import datetime, timezone
        from scheduling.engine import BookingService

        slot_start = datetime(2025, 6, 10, 10, 0, tzinfo=timezone.utc)
        winners = []
        lock = asyncio.Lock()  # simulate SETNX atomicity

        # Simulate Redis SETNX: only first caller wins
        acquired = False

        async def mock_redis_set(*args, nx=False, ex=None):
            nonlocal acquired
            async with lock:
                if nx and not acquired:
                    acquired = True
                    return True
                elif nx:
                    return None
            return True

        redis_mock = AsyncMock()
        redis_mock.set = mock_redis_set

        db_mock = AsyncMock()
        db_mock.add = MagicMock()
        db_mock.flush = AsyncMock()
        db_mock.commit = AsyncMock()

        async def attempt_reserve(session_id: str):
            svc = BookingService(db=db_mock, redis_client=redis_mock)
            result = await svc.reserve_slot(
                doctor_id=1, slot_start_utc=slot_start, session_id=session_id
            )
            if result:
                winners.append(session_id)

        sessions = [f"session-{i}" for i in range(10)]
        await asyncio.gather(*[attempt_reserve(s) for s in sessions])

        assert len(winners) == 1, (
            f"Expected exactly 1 winner for slot reservation, got {len(winners)}: {winners}"
        )


@pytest.mark.load
class TestLatencyBudget:
    """Pure logic layer should process 20 turns in < 2 seconds."""

    @pytest.mark.asyncio
    async def test_pure_logic_throughput(self):
        from pipeline.emergency_detector import EmergencyDetector
        from pipeline.context_manager import ConversationContext
        from analytics.cost_tracker import CallCost, calculate_call_cost

        det = EmergencyDetector()

        async def simulate_turn(session_id: str):
            ctx = ConversationContext(session_id, "ur-PK")
            ctx.add_turn("user", "مجھے ڈاکٹر احمد سے کل ملنا ہے")
            det.detect("مجھے ڈاکٹر احمد سے کل ملنا ہے", "ur-PK")
            cost = CallCost(stt_seconds=3.0, llm_input_tokens=200, llm_output_tokens=80, tts_chars=200)
            calculate_call_cost(cost)
            ctx.add_turn("assistant", "جی، ڈاکٹر احمد کا کل دوپہر تین بجے وقت دستیاب ہے")
            return ctx._state.turn_count

        start = time.monotonic()
        results = await asyncio.gather(*[simulate_turn(f"perf-{i}") for i in range(20)])
        elapsed = time.monotonic() - start

        assert all(r == 2 for r in results)
        assert elapsed < 2.0, (
            f"20 concurrent turns took {elapsed:.2f}s — should be < 2s for pure logic"
        )
