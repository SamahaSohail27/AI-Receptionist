"""
Per-call cost calculation.
Rates sourced from provider pricing pages (2024 averages).
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Provider rates
# ---------------------------------------------------------------------------

STT_RATES_PER_SECOND = {
    "deepgram": 0.59 / 3600,       # $0.59/hr
    "groq_whisper": 0.111 / 3600,  # $0.111/hr (extremely fast + cheap)
    "openai_whisper": 0.36 / 3600, # $0.36/hr
    "azure_speech": 1.00 / 3600,   # $1.00/hr
    "google_speech": 0.72 / 3600,
    "assemblyai": 0.65 / 3600,
}

LLM_RATES_PER_TOKEN = {
    "gpt-4o-mini": {"input": 0.15e-6, "output": 0.60e-6},
    "gpt-4o": {"input": 2.50e-6, "output": 10.00e-6},
    "claude-haiku-4-5-20251001": {"input": 0.80e-6, "output": 4.00e-6},
    "claude-sonnet-4-6": {"input": 3.00e-6, "output": 15.00e-6},
    "llama-3.3-70b-versatile": {"input": 0.59e-6, "output": 0.79e-6},
    "gemini-2.0-flash": {"input": 0.10e-6, "output": 0.40e-6},
}

TTS_RATES_PER_CHAR = {
    "azure": 16.00 / 1_000_000,       # $16/1M chars
    "openai": 15.00 / 1_000_000,      # $15/1M chars (tts-1)
    "elevenlabs": 0.22 / 1_000,       # $0.22/1K chars
    "google": 16.00 / 1_000_000,
    "cartesia": 0.065 / 1_000,
}

TELEPHONY_RATES_PER_MINUTE = {
    "plivo": 0.0085,
    "twilio": 0.0085,
    "telnyx": 0.009,
}


# ---------------------------------------------------------------------------
# Cost tracker
# ---------------------------------------------------------------------------

@dataclass
class CallCost:
    stt_seconds: float = 0.0
    stt_provider: str = "deepgram"
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_model: str = "gpt-4o-mini"
    tts_chars: int = 0
    tts_provider: str = "azure"
    telephony_seconds: float = 0.0
    telephony_provider: str = "plivo"
    tts_cache_hits: int = 0  # Each cache hit saves one TTS synthesis — subtract from chars


def calculate_call_cost(cost: CallCost) -> dict:
    """Return per-component and total USD cost for a call."""
    # STT cost
    rate_stt = STT_RATES_PER_SECOND.get(cost.stt_provider, 0.0)
    cost_stt = cost.stt_seconds * rate_stt

    # LLM cost
    rates_llm = LLM_RATES_PER_TOKEN.get(cost.llm_model, {"input": 0.0, "output": 0.0})
    cost_llm = (cost.llm_input_tokens * rates_llm["input"]) + (cost.llm_output_tokens * rates_llm["output"])

    # TTS cost (cache hits are free — only charge for synthesized chars)
    rate_tts = TTS_RATES_PER_CHAR.get(cost.tts_provider, 0.0)
    billable_chars = max(0, cost.tts_chars - (cost.tts_cache_hits * 50))  # avg 50 chars per cached phrase
    cost_tts = billable_chars * rate_tts

    # Telephony cost
    rate_tel = TELEPHONY_RATES_PER_MINUTE.get(cost.telephony_provider, 0.0)
    cost_telephony = (cost.telephony_seconds / 60.0) * rate_tel

    total = cost_stt + cost_llm + cost_tts + cost_telephony

    return {
        "stt_usd": round(cost_stt, 6),
        "llm_usd": round(cost_llm, 6),
        "tts_usd": round(cost_tts, 6),
        "telephony_usd": round(cost_telephony, 6),
        "total_usd": round(total, 6),
    }
