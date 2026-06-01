"""
Provider registry and factory.

Instantiate any STT/LLM/TTS/Telephony provider by string key.
Runtime switching: update ProviderConfig in DB → call registry.reload() → next call uses new provider.
"""
from __future__ import annotations

import os
from typing import Any

from providers.base import BaseSTT, BaseLLM, BaseTTS, BaseTelephony
from providers.language_profile import LanguageProfile, get_profile


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ProviderRegistry:
    """
    Central factory for all provider adapters.
    Import is deferred per-provider so missing SDK installs don't crash startup
    for providers not in use.
    """

    # -----------------------------------------------------------------------
    # STT
    # -----------------------------------------------------------------------
    def make_stt(
        self,
        provider: str,
        language_code: str,
        confidence_threshold: float,
        **kwargs: Any,
    ) -> BaseSTT:
        p = provider.lower()
        if p == "deepgram":
            from providers.stt.deepgram import DeepgramSTTAdapter
            return DeepgramSTTAdapter(language_code, confidence_threshold, **kwargs)
        if p in ("groq_whisper", "groq"):
            from providers.stt.groq_whisper import GroqWhisperSTTAdapter
            return GroqWhisperSTTAdapter(language_code, confidence_threshold, **kwargs)
        if p == "openai_whisper":
            from providers.stt.openai_whisper import OpenAIWhisperSTTAdapter
            return OpenAIWhisperSTTAdapter(language_code, confidence_threshold, **kwargs)
        if p == "azure":
            from providers.stt.azure_speech import AzureSpeechSTTAdapter
            return AzureSpeechSTTAdapter(language_code, confidence_threshold, **kwargs)
        if p == "google":
            from providers.stt.google_speech import GoogleSTTAdapter
            return GoogleSTTAdapter(language_code, confidence_threshold, **kwargs)
        if p == "assemblyai":
            from providers.stt.assemblyai import AssemblyAISTTAdapter
            return AssemblyAISTTAdapter(language_code, confidence_threshold, **kwargs)
        raise ValueError(f"Unknown STT provider: {provider!r}")

    # -----------------------------------------------------------------------
    # LLM
    # -----------------------------------------------------------------------
    def make_llm(
        self,
        provider: str,
        model_tier: str = "fast",
        **kwargs: Any,
    ) -> BaseLLM:
        p = provider.lower()
        if p == "openai":
            from providers.llm.openai import OpenAILLMAdapter
            return OpenAILLMAdapter(model_tier=model_tier, **kwargs)
        if p == "anthropic":
            from providers.llm.anthropic import AnthropicLLMAdapter
            return AnthropicLLMAdapter(model_tier=model_tier, **kwargs)
        if p == "gemini":
            from providers.llm.gemini import GeminiLLMAdapter
            return GeminiLLMAdapter(model_tier=model_tier, **kwargs)
        if p == "groq":
            from providers.llm.groq import GroqLLMAdapter
            return GroqLLMAdapter(model_tier=model_tier, **kwargs)
        raise ValueError(f"Unknown LLM provider: {provider!r}")

    # -----------------------------------------------------------------------
    # TTS
    # -----------------------------------------------------------------------
    def make_tts(
        self,
        provider: str,
        language_code: str,
        voice: str,
        **kwargs: Any,
    ) -> BaseTTS:
        p = provider.lower()
        if p == "azure":
            from providers.tts.azure_neural import AzureNeuralTTSAdapter
            return AzureNeuralTTSAdapter(language_code, voice, **kwargs)
        if p == "openai":
            from providers.tts.openai_tts import OpenAITTSAdapter
            return OpenAITTSAdapter(language_code, voice, **kwargs)
        if p == "elevenlabs":
            from providers.tts.elevenlabs import ElevenLabsTTSAdapter
            return ElevenLabsTTSAdapter(language_code, voice, **kwargs)
        if p == "google":
            from providers.tts.google_tts import GoogleTTSAdapter
            return GoogleTTSAdapter(language_code, voice, **kwargs)
        if p == "cartesia":
            from providers.tts.cartesia import CartesiaTTSAdapter
            return CartesiaTTSAdapter(language_code, voice, **kwargs)
        raise ValueError(f"Unknown TTS provider: {provider!r}")

    # -----------------------------------------------------------------------
    # Telephony
    # -----------------------------------------------------------------------
    def make_telephony(self, provider: str, **kwargs: Any) -> BaseTelephony:
        p = provider.lower()
        if p == "plivo":
            from providers.telephony.plivo import PlivoAdapter
            return PlivoAdapter(**kwargs)
        if p == "twilio":
            from providers.telephony.twilio import TwilioAdapter
            return TwilioAdapter(**kwargs)
        if p == "telnyx":
            from providers.telephony.telnyx import TelnyxAdapter
            return TelnyxAdapter(**kwargs)
        raise ValueError(f"Unknown telephony provider: {provider!r}")

    # -----------------------------------------------------------------------
    # Convenience: build all adapters for a LanguageProfile in one call
    # -----------------------------------------------------------------------
    def make_for_profile(
        self,
        profile: LanguageProfile,
        llm_provider: str = "openai",
        llm_tier: str = "fast",
        telephony_provider: str = "plivo",
    ) -> dict:
        stt = self.make_stt(
            provider=profile.stt.provider,
            language_code=profile.stt.language_code,
            confidence_threshold=profile.stt.confidence_threshold,
        )
        tts = self.make_tts(
            provider=profile.tts.provider,
            language_code=profile.tts.language_code,
            voice=profile.tts.voice,
        )
        llm = self.make_llm(provider=llm_provider, model_tier=llm_tier)
        telephony = self.make_telephony(provider=telephony_provider)
        return {"stt": stt, "tts": tts, "llm": llm, "telephony": telephony}


# Module-level singleton
registry = ProviderRegistry()
