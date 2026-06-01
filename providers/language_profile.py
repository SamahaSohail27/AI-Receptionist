"""
Language profile system — maps language codes to provider config.

DTMF key → language code → LanguageProfile (STT + LLM + TTS + noise words + number converter)

Three supported languages:
  ur-PK  Pakistani Urdu   DTMF 1
  pa-PK  Punjabi          DTMF 2
  en     English          DTMF 3
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class STTConfig:
    provider: str
    language_code: str
    confidence_threshold: float
    model: str = ""
    endpointing_ms: int = 600
    utterance_end_ms: int = 1500


@dataclass
class TTSConfig:
    provider: str
    voice: str
    language_code: str
    speaking_rate: float = 1.0


@dataclass
class LanguageProfile:
    code: str
    dtmf_key: str
    stt: STTConfig
    tts: TTSConfig
    llm_prompt_variant: str
    noise_words: list[str] = field(default_factory=list)
    number_converter: str = "english"
    is_rtl: bool = False
    tts_fallback_note: str = ""


# ---------------------------------------------------------------------------
# Canonical profile definitions — single source of truth
# ---------------------------------------------------------------------------

LANGUAGE_PROFILES: dict[str, LanguageProfile] = {
    "ur-PK": LanguageProfile(
        code="ur-PK",
        dtmf_key="1",
        stt=STTConfig(
            provider="deepgram",
            language_code="ur",
            confidence_threshold=0.45,  # SACRED — do not raise
            model="nova-2",
            endpointing_ms=600,
            utterance_end_ms=1500,
        ),
        tts=TTSConfig(
            provider="azure",
            voice="ur-PK-UzmaNeural",
            language_code="ur-PK",
        ),
        llm_prompt_variant="ur",
        noise_words=["آہ", "ہاں", "اچھا", "جی", "ٹھیک ہے", "ام", "ہممم", "آں"],
        number_converter="urdu",
        is_rtl=True,
    ),
    "pa-PK": LanguageProfile(
        code="pa-PK",
        dtmf_key="2",
        stt=STTConfig(
            provider="groq_whisper",
            language_code="pa",
            confidence_threshold=0.40,  # SACRED — do not raise
            model="whisper-large-v3",
            endpointing_ms=600,
            utterance_end_ms=1500,
        ),
        tts=TTSConfig(
            # No dedicated Pakistani Punjabi TTS exists — Azure ur-PK fallback is
            # clinically acceptable; Pakistani Punjabi speakers understand Urdu TTS.
            provider="azure",
            voice="ur-PK-UzmaNeural",
            language_code="ur-PK",
        ),
        llm_prompt_variant="pa",
        noise_words=["اوئے", "ہاں", "ٹھیک اے", "اچھا", "جی", "ہمم"],
        number_converter="urdu",  # Punjabi numbers delegate to Urdu converter for TTS
        is_rtl=True,
        tts_fallback_note="Dedicated Pakistani Punjabi TTS unavailable — using Azure ur-PK-UzmaNeural",
    ),
    "en": LanguageProfile(
        code="en",
        dtmf_key="3",
        stt=STTConfig(
            provider="deepgram",
            language_code="en-US",
            confidence_threshold=0.70,  # SACRED — do not lower
            model="nova-2",
            endpointing_ms=600,
            utterance_end_ms=1500,
        ),
        tts=TTSConfig(
            provider="openai",
            voice="nova",
            language_code="en",
        ),
        llm_prompt_variant="en",
        noise_words=["um", "uh", "hmm", "like", "you know", "er", "ah"],
        number_converter="english",
        is_rtl=False,
    ),
}

# Reverse map: DTMF digit → language code
DTMF_TO_LANGUAGE: dict[str, str] = {
    profile.dtmf_key: code
    for code, profile in LANGUAGE_PROFILES.items()
}


def get_profile(language_code: str) -> LanguageProfile:
    """Return language profile for given code. Falls back to ur-PK."""
    return LANGUAGE_PROFILES.get(language_code, LANGUAGE_PROFILES["ur-PK"])


def get_profile_for_dtmf(digit: str) -> LanguageProfile:
    """Return language profile triggered by DTMF digit (1/2/3)."""
    code = DTMF_TO_LANGUAGE.get(digit, "ur-PK")
    return LANGUAGE_PROFILES[code]
