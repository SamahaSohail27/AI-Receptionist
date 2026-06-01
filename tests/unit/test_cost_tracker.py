"""
Unit tests — cost calculator.

Verifies:
- Zero-cost edge cases
- Per-provider rate calculations
- TTS cache hits reduce billable characters
- Telephony cost based on duration
"""
import pytest

from analytics.cost_tracker import (
    CallCost,
    calculate_call_cost,
    STT_RATES_PER_SECOND,
    LLM_RATES_PER_TOKEN,
    TTS_RATES_PER_CHAR,
    TELEPHONY_RATES_PER_MINUTE,
)


@pytest.mark.unit
class TestCostTracker:

    def test_zero_cost_empty_call(self):
        cost = CallCost(
            stt_seconds=0, stt_provider="deepgram",
            llm_input_tokens=0, llm_output_tokens=0, llm_model="gpt-4o-mini",
            tts_chars=0, tts_provider="azure",
            telephony_seconds=0, telephony_provider="plivo",
        )
        result = calculate_call_cost(cost)
        assert result["total_usd"] == 0.0

    def test_stt_deepgram_cost(self):
        seconds = 60.0
        rate = STT_RATES_PER_SECOND["deepgram"]
        cost = CallCost(stt_seconds=seconds, stt_provider="deepgram")
        result = calculate_call_cost(cost)
        expected = round(seconds * rate, 6)
        assert result["stt_usd"] == expected

    def test_stt_groq_whisper_cheaper_than_deepgram(self):
        cost_dgram = CallCost(stt_seconds=60.0, stt_provider="deepgram")
        cost_groq = CallCost(stt_seconds=60.0, stt_provider="groq_whisper")
        assert calculate_call_cost(cost_groq)["stt_usd"] < calculate_call_cost(cost_dgram)["stt_usd"]

    def test_llm_gpt4o_mini_cost(self):
        cost = CallCost(
            llm_input_tokens=500, llm_output_tokens=200, llm_model="gpt-4o-mini"
        )
        result = calculate_call_cost(cost)
        rates = LLM_RATES_PER_TOKEN["gpt-4o-mini"]
        expected = round(500 * rates["input"] + 200 * rates["output"], 6)
        assert result["llm_usd"] == expected

    def test_llm_gpt4o_more_expensive_than_mini(self):
        tokens = dict(llm_input_tokens=1000, llm_output_tokens=500)
        cost_mini = CallCost(**tokens, llm_model="gpt-4o-mini")
        cost_full = CallCost(**tokens, llm_model="gpt-4o")
        assert calculate_call_cost(cost_full)["llm_usd"] > calculate_call_cost(cost_mini)["llm_usd"]

    def test_tts_azure_cost(self):
        chars = 1000
        rate = TTS_RATES_PER_CHAR["azure"]
        cost = CallCost(tts_chars=chars, tts_provider="azure", tts_cache_hits=0)
        result = calculate_call_cost(cost)
        expected = round(chars * rate, 6)
        assert result["tts_usd"] == expected

    def test_tts_cache_hits_reduce_billable_chars(self):
        cost_no_cache = CallCost(tts_chars=500, tts_provider="azure", tts_cache_hits=0)
        cost_with_cache = CallCost(tts_chars=500, tts_provider="azure", tts_cache_hits=5)
        no_cache_result = calculate_call_cost(cost_no_cache)
        with_cache_result = calculate_call_cost(cost_with_cache)
        assert with_cache_result["tts_usd"] < no_cache_result["tts_usd"]

    def test_tts_cache_hits_cannot_produce_negative_cost(self):
        # 1000 cache hits on 100 chars — billable should floor at 0
        cost = CallCost(tts_chars=100, tts_provider="azure", tts_cache_hits=1000)
        result = calculate_call_cost(cost)
        assert result["tts_usd"] >= 0.0

    def test_telephony_plivo_cost_60s(self):
        cost = CallCost(telephony_seconds=60.0, telephony_provider="plivo")
        result = calculate_call_cost(cost)
        expected = round(1.0 * TELEPHONY_RATES_PER_MINUTE["plivo"], 6)
        assert result["telephony_usd"] == expected

    def test_telephony_telnyx_more_expensive_than_plivo(self):
        assert TELEPHONY_RATES_PER_MINUTE["telnyx"] > TELEPHONY_RATES_PER_MINUTE["plivo"]

    def test_total_is_sum_of_components(self):
        cost = CallCost(
            stt_seconds=120.0, stt_provider="deepgram",
            llm_input_tokens=800, llm_output_tokens=300, llm_model="gpt-4o-mini",
            tts_chars=1200, tts_provider="azure", tts_cache_hits=3,
            telephony_seconds=120.0, telephony_provider="plivo",
        )
        result = calculate_call_cost(cost)
        component_sum = round(
            result["stt_usd"] + result["llm_usd"] + result["tts_usd"] + result["telephony_usd"],
            6,
        )
        assert abs(result["total_usd"] - component_sum) < 1e-9

    def test_unknown_provider_defaults_to_zero_rate(self):
        cost = CallCost(stt_seconds=60.0, stt_provider="unknown_provider_xyz")
        result = calculate_call_cost(cost)
        assert result["stt_usd"] == 0.0

    def test_result_keys_present(self):
        result = calculate_call_cost(CallCost())
        assert set(result.keys()) == {"stt_usd", "llm_usd", "tts_usd", "telephony_usd", "total_usd"}

    def test_elevenlabs_tts_expensive_per_char(self):
        cost = CallCost(tts_chars=1000, tts_provider="elevenlabs", tts_cache_hits=0)
        result = calculate_call_cost(cost)
        expected = round(1000 * TTS_RATES_PER_CHAR["elevenlabs"], 6)
        assert result["tts_usd"] == expected

    def test_real_world_2_minute_urdu_call(self):
        cost = CallCost(
            stt_seconds=120.0, stt_provider="deepgram",
            llm_input_tokens=600, llm_output_tokens=250, llm_model="gpt-4o-mini",
            tts_chars=800, tts_provider="azure", tts_cache_hits=4,
            telephony_seconds=120.0, telephony_provider="plivo",
        )
        result = calculate_call_cost(cost)
        # A 2-minute Urdu call should cost well under $0.05
        assert result["total_usd"] < 0.05
        assert result["total_usd"] > 0.0
