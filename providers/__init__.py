"""
Provider abstraction layer.

Use the registry singleton to instantiate any provider by string key:

    from providers.registry import registry
    stt = registry.make_stt("deepgram", "ur", 0.45)
    tts = registry.make_tts("azure", "ur-PK", "ur-PK-UzmaNeural")

Or build all adapters for a language profile at once:

    from providers.language_profile import get_profile
    profile = get_profile("ur-PK")
    adapters = registry.make_for_profile(profile)
"""
from providers.base import (
    BaseSTT,
    BaseLLM,
    BaseTTS,
    BaseTelephony,
    STTResult,
    LLMMessage,
    LLMResponse,
    TTSResult,
    CallTransfer,
)
from providers.language_profile import (
    LanguageProfile,
    LANGUAGE_PROFILES,
    DTMF_TO_LANGUAGE,
    get_profile,
    get_profile_for_dtmf,
)
from providers.registry import registry

__all__ = [
    "BaseSTT", "BaseLLM", "BaseTTS", "BaseTelephony",
    "STTResult", "LLMMessage", "LLMResponse", "TTSResult", "CallTransfer",
    "LanguageProfile", "LANGUAGE_PROFILES", "DTMF_TO_LANGUAGE",
    "get_profile", "get_profile_for_dtmf",
    "registry",
]
