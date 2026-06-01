"""
Unit tests — language profile system.

Verifies:
- All 3 languages defined with correct DTMF keys
- Sacred STT confidence thresholds enforced
- DTMF reverse mapping correct
- RTL flag correct per language
- TTS provider correct (Punjabi falls back to Azure ur-PK-UzmaNeural)
"""
import pytest

from providers.language_profile import (
    DTMF_TO_LANGUAGE,
    LANGUAGE_PROFILES,
    get_profile,
    get_profile_for_dtmf,
)


@pytest.mark.unit
class TestLanguageProfiles:

    def test_all_three_languages_defined(self):
        assert "ur-PK" in LANGUAGE_PROFILES
        assert "pa-PK" in LANGUAGE_PROFILES
        assert "en" in LANGUAGE_PROFILES

    def test_dtmf_keys_unique(self):
        keys = [p.dtmf_key for p in LANGUAGE_PROFILES.values()]
        assert len(keys) == len(set(keys)), "DTMF keys must be unique"

    def test_dtmf_mapping_correct(self):
        assert DTMF_TO_LANGUAGE["1"] == "ur-PK"
        assert DTMF_TO_LANGUAGE["2"] == "pa-PK"
        assert DTMF_TO_LANGUAGE["3"] == "en"

    # -----------------------------------------------------------------------
    # Sacred STT confidence thresholds
    # -----------------------------------------------------------------------

    def test_urdu_stt_confidence_threshold_sacred(self):
        profile = LANGUAGE_PROFILES["ur-PK"]
        assert profile.stt.confidence_threshold == 0.45, (
            "SACRED: Urdu STT confidence threshold must be 0.45"
        )

    def test_punjabi_stt_confidence_threshold_sacred(self):
        profile = LANGUAGE_PROFILES["pa-PK"]
        assert profile.stt.confidence_threshold == 0.40, (
            "SACRED: Punjabi STT confidence threshold must be 0.40"
        )

    def test_english_stt_confidence_threshold_sacred(self):
        profile = LANGUAGE_PROFILES["en"]
        assert profile.stt.confidence_threshold == 0.70, (
            "SACRED: English STT confidence threshold must be 0.70"
        )

    # -----------------------------------------------------------------------
    # TTS provider assignments
    # -----------------------------------------------------------------------

    def test_urdu_tts_is_azure_uzma(self):
        p = LANGUAGE_PROFILES["ur-PK"]
        assert p.tts.provider == "azure"
        assert p.tts.voice == "ur-PK-UzmaNeural"

    def test_punjabi_tts_falls_back_to_azure_uzma(self):
        p = LANGUAGE_PROFILES["pa-PK"]
        assert p.tts.provider == "azure"
        assert p.tts.voice == "ur-PK-UzmaNeural", (
            "No dedicated Punjabi TTS — must fall back to ur-PK-UzmaNeural"
        )

    def test_english_tts_is_openai_nova(self):
        p = LANGUAGE_PROFILES["en"]
        assert p.tts.provider == "openai"
        assert p.tts.voice == "nova"

    # -----------------------------------------------------------------------
    # RTL flags
    # -----------------------------------------------------------------------

    def test_urdu_is_rtl(self):
        assert LANGUAGE_PROFILES["ur-PK"].is_rtl is True

    def test_punjabi_is_rtl(self):
        assert LANGUAGE_PROFILES["pa-PK"].is_rtl is True

    def test_english_is_not_rtl(self):
        assert LANGUAGE_PROFILES["en"].is_rtl is False

    # -----------------------------------------------------------------------
    # STT provider assignments
    # -----------------------------------------------------------------------

    def test_urdu_stt_is_deepgram(self):
        p = LANGUAGE_PROFILES["ur-PK"]
        assert p.stt.provider == "deepgram"
        assert p.stt.model == "nova-2"

    def test_punjabi_stt_is_groq_whisper(self):
        p = LANGUAGE_PROFILES["pa-PK"]
        assert p.stt.provider == "groq_whisper"
        assert p.stt.model == "whisper-large-v3"

    def test_english_stt_is_deepgram(self):
        p = LANGUAGE_PROFILES["en"]
        assert p.stt.provider == "deepgram"
        assert p.stt.language_code == "en-US"

    # -----------------------------------------------------------------------
    # Helper functions
    # -----------------------------------------------------------------------

    def test_get_profile_returns_correct_profile(self):
        p = get_profile("ur-PK")
        assert p.code == "ur-PK"

    def test_get_profile_unknown_falls_back_to_urdu(self):
        p = get_profile("xx-XX")
        assert p.code == "ur-PK"

    def test_get_profile_for_dtmf_1_is_urdu(self):
        p = get_profile_for_dtmf("1")
        assert p.code == "ur-PK"

    def test_get_profile_for_dtmf_2_is_punjabi(self):
        p = get_profile_for_dtmf("2")
        assert p.code == "pa-PK"

    def test_get_profile_for_dtmf_3_is_english(self):
        p = get_profile_for_dtmf("3")
        assert p.code == "en"

    def test_get_profile_for_dtmf_unknown_falls_back_to_urdu(self):
        p = get_profile_for_dtmf("9")
        assert p.code == "ur-PK"

    # -----------------------------------------------------------------------
    # Noise words defined for all 3 languages
    # -----------------------------------------------------------------------

    def test_urdu_noise_words_non_empty(self):
        assert len(LANGUAGE_PROFILES["ur-PK"].noise_words) > 0

    def test_punjabi_noise_words_non_empty(self):
        assert len(LANGUAGE_PROFILES["pa-PK"].noise_words) > 0

    def test_english_noise_words_include_um_uh(self):
        noise = LANGUAGE_PROFILES["en"].noise_words
        assert "um" in noise
        assert "uh" in noise

    # -----------------------------------------------------------------------
    # Deepgram endpointing params
    # -----------------------------------------------------------------------

    def test_urdu_deepgram_endpointing(self):
        stt = LANGUAGE_PROFILES["ur-PK"].stt
        assert stt.endpointing_ms == 600
        assert stt.utterance_end_ms == 1500

    def test_english_deepgram_endpointing(self):
        stt = LANGUAGE_PROFILES["en"].stt
        assert stt.endpointing_ms == 600
        assert stt.utterance_end_ms == 1500
