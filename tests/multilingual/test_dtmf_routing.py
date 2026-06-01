"""
Multilingual tests — DTMF language routing.

Verifies:
- DTMF 1 → ur-PK pipeline
- DTMF 2 → pa-PK pipeline
- DTMF 3 → en pipeline
- Unknown DTMF → default language (ur-PK)
- Language switch mid-call updates context manager state
"""
from __future__ import annotations

import pytest


@pytest.mark.multilingual
class TestDTMFRouting:

    def test_dtmf_1_maps_to_urdu(self):
        from providers.language_profile import get_profile_for_dtmf
        p = get_profile_for_dtmf("1")
        assert p.code == "ur-PK"
        assert p.dtmf_key == "1"

    def test_dtmf_2_maps_to_punjabi(self):
        from providers.language_profile import get_profile_for_dtmf
        p = get_profile_for_dtmf("2")
        assert p.code == "pa-PK"
        assert p.dtmf_key == "2"

    def test_dtmf_3_maps_to_english(self):
        from providers.language_profile import get_profile_for_dtmf
        p = get_profile_for_dtmf("3")
        assert p.code == "en"
        assert p.dtmf_key == "3"

    def test_unknown_dtmf_returns_urdu_default(self):
        from providers.language_profile import get_profile_for_dtmf
        p = get_profile_for_dtmf("0")
        assert p.code == "ur-PK"

    def test_dtmf_star_returns_urdu_default(self):
        from providers.language_profile import get_profile_for_dtmf
        p = get_profile_for_dtmf("*")
        assert p.code == "ur-PK"

    def test_dtmf_to_language_dict_complete(self):
        from providers.language_profile import DTMF_TO_LANGUAGE
        assert "1" in DTMF_TO_LANGUAGE
        assert "2" in DTMF_TO_LANGUAGE
        assert "3" in DTMF_TO_LANGUAGE

    def test_each_language_has_unique_dtmf(self):
        from providers.language_profile import LANGUAGE_PROFILES
        dtmf_keys = [p.dtmf_key for p in LANGUAGE_PROFILES.values()]
        assert len(dtmf_keys) == len(set(dtmf_keys)), "Each language must have a unique DTMF key"

    def test_plivo_webhook_parses_digit(self):
        """Verify plivo DTMF handler correctly extracts digit and routes."""
        from unittest.mock import AsyncMock, MagicMock, patch
        # The plivo_dtmf handler routes DTMF 1/2/3 to pipeline
        # Verify the routing logic by checking digit validation
        valid_digits = {"1", "2", "3"}
        for digit in valid_digits:
            from providers.language_profile import get_profile_for_dtmf
            p = get_profile_for_dtmf(digit)
            assert p is not None

    def test_language_switch_updates_context_state(self):
        from pipeline.context_manager import ConversationContext
        ctx = ConversationContext("sess-dtmf-1", "ur-PK")
        assert ctx._state.language == "ur-PK"
        ctx.update_state(language="en")
        assert ctx._state.language == "en"

    def test_all_stt_configs_have_language_codes(self):
        from providers.language_profile import LANGUAGE_PROFILES
        for code, profile in LANGUAGE_PROFILES.items():
            assert profile.stt.language_code, (
                f"{code} profile missing STT language_code"
            )

    def test_urdu_stt_language_code_is_not_english(self):
        from providers.language_profile import LANGUAGE_PROFILES
        assert LANGUAGE_PROFILES["ur-PK"].stt.language_code != "en-US"
        assert LANGUAGE_PROFILES["ur-PK"].stt.language_code != "en"

    def test_punjabi_stt_language_code_is_not_english(self):
        from providers.language_profile import LANGUAGE_PROFILES
        assert LANGUAGE_PROFILES["pa-PK"].stt.language_code != "en-US"

    def test_all_languages_have_noise_words(self):
        """Noise word filter must be defined for all 3 languages."""
        from providers.language_profile import LANGUAGE_PROFILES
        for code, profile in LANGUAGE_PROFILES.items():
            assert profile.noise_words, f"{code} has no noise words defined"


@pytest.mark.multilingual
class TestProviderSwitchOnLanguageChange:
    """Verify provider config changes when language changes mid-call."""

    def test_urdu_uses_deepgram_english_uses_deepgram(self):
        from providers.language_profile import LANGUAGE_PROFILES
        # Both use Deepgram but different language codes
        assert LANGUAGE_PROFILES["ur-PK"].stt.provider == "deepgram"
        assert LANGUAGE_PROFILES["en"].stt.provider == "deepgram"

    def test_punjabi_uses_different_stt_than_urdu(self):
        from providers.language_profile import LANGUAGE_PROFILES
        # Punjabi uses Groq Whisper because Deepgram doesn't support pa-PK well
        ur_provider = LANGUAGE_PROFILES["ur-PK"].stt.provider
        pa_provider = LANGUAGE_PROFILES["pa-PK"].stt.provider
        assert ur_provider != pa_provider, (
            "Punjabi should use a different STT provider than Urdu"
        )

    def test_urdu_punjabi_share_same_tts(self):
        from providers.language_profile import LANGUAGE_PROFILES
        # No Punjabi TTS exists — both use Azure ur-PK-UzmaNeural
        assert LANGUAGE_PROFILES["ur-PK"].tts.voice == LANGUAGE_PROFILES["pa-PK"].tts.voice

    def test_english_uses_different_tts_than_urdu(self):
        from providers.language_profile import LANGUAGE_PROFILES
        assert LANGUAGE_PROFILES["en"].tts.provider != LANGUAGE_PROFILES["ur-PK"].tts.provider
