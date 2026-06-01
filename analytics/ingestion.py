"""
Call event ingestion — consumes structured events during call lifecycle and writes to DB.

All DB writes are non-blocking: run_in_executor to avoid blocking the async event loop.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class CallEventIngester:
    """
    Ingests call lifecycle events and writes CallLog + Transcript records.

    Usage:
        ingester = CallEventIngester(db_session)
        await ingester.on_call_started(session_id, caller_phone, language, provider_chain)
        await ingester.on_turn(session_id, speaker, text, confidence, latency_dict)
        await ingester.on_call_ended(session_id, outcome, cost_dict, metrics)
    """

    def __init__(self, db_factory) -> None:
        # db_factory: callable that returns a new AsyncSession (used in executor)
        self._db_factory = db_factory
        self._active: dict[str, dict] = {}  # session_id → in-progress call data

    async def on_call_started(
        self,
        session_id: str,
        caller_phone: str,
        language: str,
        telephony_provider: str,
        stt_provider: str,
        llm_provider: str,
        tts_provider: str,
        llm_model: str,
    ) -> None:
        caller_phone_hash = hashlib.sha256(caller_phone.encode()).hexdigest()
        self._active[session_id] = {
            "session_id": session_id,
            "caller_phone_hash": caller_phone_hash,
            "language": language,
            "telephony_provider": telephony_provider,
            "stt_provider": stt_provider,
            "llm_provider": llm_provider,
            "tts_provider": tts_provider,
            "llm_model": llm_model,
            "started_at": datetime.now(timezone.utc),
            "turns": [],
            "stt_latencies": [],
            "llm_latencies": [],
            "tts_latencies": [],
        }
        logger.info("Call started: session=%s language=%s", session_id, language)

    async def on_turn(
        self,
        session_id: str,
        speaker: str,
        text: str,
        language: str,
        is_rtl: bool,
        stt_confidence: float | None = None,
        stt_provider: str | None = None,
        latency: dict | None = None,
    ) -> None:
        call = self._active.get(session_id)
        if call is None:
            return
        turn = {
            "turn_id": len(call["turns"]) + 1,
            "speaker": speaker,
            "text": text,
            "language": language,
            "is_rtl": is_rtl,
            "stt_confidence": stt_confidence,
            "stt_provider": stt_provider,
            "latency": latency or {},
        }
        call["turns"].append(turn)
        if latency:
            if "stt_ms" in latency:
                call["stt_latencies"].append(latency["stt_ms"])
            if "llm_ms" in latency:
                call["llm_latencies"].append(latency["llm_ms"])
            if "tts_ms" in latency:
                call["tts_latencies"].append(latency["tts_ms"])

    async def on_call_ended(
        self,
        session_id: str,
        outcome: str,
        intent: str | None,
        patient_id: int | None,
        appointment_id: int | None,
        escalation_triggered: bool,
        emergency_detected: bool,
        tts_cache_hits: int,
        tts_cache_misses: int,
        cost: dict,
    ) -> None:
        call = self._active.pop(session_id, None)
        if call is None:
            logger.warning("on_call_ended for unknown session: %s", session_id)
            return

        ended_at = datetime.now(timezone.utc)
        duration = int((ended_at - call["started_at"]).total_seconds())

        def _avg(lst): return int(sum(lst) / len(lst)) if lst else None
        def _p95(lst):
            if not lst: return None
            s = sorted(lst)
            return s[int(len(s) * 0.95)]

        call_data = {
            "session_id": session_id,
            "patient_id": patient_id,
            "caller_phone_hash": call["caller_phone_hash"],
            "language": call["language"],
            "intent": intent,
            "outcome": outcome,
            "telephony_provider": call["telephony_provider"],
            "stt_provider": call["stt_provider"],
            "llm_provider": call["llm_provider"],
            "tts_provider": call["tts_provider"],
            "llm_model": call["llm_model"],
            "started_at": call["started_at"],
            "ended_at": ended_at,
            "duration_seconds": duration,
            "turn_count": len(call["turns"]),
            "avg_stt_ms": _avg(call["stt_latencies"]),
            "avg_llm_ms": _avg(call["llm_latencies"]),
            "avg_tts_ms": _avg(call["tts_latencies"]),
            "avg_total_ms": _avg([
                s + l + t
                for s, l, t in zip(call["stt_latencies"], call["llm_latencies"], call["tts_latencies"])
            ] if call["stt_latencies"] else []),
            "p95_total_ms": _p95(call["stt_latencies"]),
            "tts_cache_hits": tts_cache_hits,
            "tts_cache_misses": tts_cache_misses,
            "cost_stt_usd": cost.get("stt_usd"),
            "cost_llm_usd": cost.get("llm_usd"),
            "cost_tts_usd": cost.get("tts_usd"),
            "cost_telephony_usd": cost.get("telephony_usd"),
            "cost_total_usd": cost.get("total_usd"),
            "escalation_triggered": escalation_triggered,
            "emergency_detected": emergency_detected,
            "appointment_id": appointment_id,
        }
        turns = call["turns"]

        # Write to DB non-blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._write_to_db, call_data, turns
        )

    def _write_to_db(self, call_data: dict, turns: list[dict]) -> None:
        """Synchronous DB write — runs in thread executor."""
        import asyncio
        import concurrent.futures

        async def _async_write():
            from models.call_log import CallLog, Transcript
            from sqlalchemy import text

            async with self._db_factory() as db:
                log = CallLog(**{k: v for k, v in call_data.items()})
                db.add(log)
                await db.flush()

                for turn in turns:
                    # PHI masking: replace phone numbers and CNIC patterns
                    masked = _mask_phi(turn["text"])
                    t = Transcript(
                        call_log_id=log.id,
                        session_id=call_data["session_id"],
                        turn_id=turn["turn_id"],
                        speaker=turn["speaker"],
                        language=turn["language"],
                        is_rtl=turn["is_rtl"],
                        raw_text=turn["text"],
                        masked_text=masked,
                        stt_confidence=turn.get("stt_confidence"),
                        stt_provider=turn.get("stt_provider"),
                    )
                    db.add(t)
                await db.commit()

        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_async_write())
        except Exception as e:
            logger.error("Failed to write call log: %s", e)
        finally:
            loop.close()


def _mask_phi(text: str) -> str:
    """Replace phone numbers and CNIC patterns with tokens."""
    import re
    # Pakistani phone: +92XXXXXXXXXX or 03XXXXXXXXX
    text = re.sub(r'\+92\d{10}', '[PHONE]', text)
    text = re.sub(r'\b0\d{10}\b', '[PHONE]', text)
    # CNIC: XXXXX-XXXXXXX-X
    text = re.sub(r'\b\d{5}-\d{7}-\d\b', '[CNIC]', text)
    return text
