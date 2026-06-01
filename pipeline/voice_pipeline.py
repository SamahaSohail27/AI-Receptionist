"""
Main voice pipeline — one instance per active call (telephony OR browser test).

Architecture (pipecat 0.0.85) — transport-agnostic:

    transport.input()                       ← Telephony WS (Twilio/Plivo mulaw)
        ↓                                     OR browser WS (PCM16 over JSON)
    AudioLevelMonitor                         (VAD lives on the transport)
        ↓
    DTMFLanguageSelector  → arms selection timer, switches downstream profile
        ↓
    STT (Deepgram or Groq Whisper per profile)
        ↓
    ConfidenceFilteredSTT  → drops low-confidence / noise-word transcripts
        ↓
    STTBroadcaster  → ws_manager admin dashboard fan-out (still passes through)
        ↓
    LLM (OpenAI, tool_choice="auto", spoken_text-first tools)
        ↓
    ResponseBroadcaster
        ↓
    TTSCacheGate  → cache hit emits AudioRawFrame, miss falls through
        ↓
    TTS (Azure / ElevenLabs / OpenAI per admin selection)
        ↓
    TTSCacheCapture  → stores fresh synthesis under cache key
        ↓
    LatencyTracker
        ↓
    transport.output()                      ← same WS, return path

Critical optimizations (all MUST be active):
  V1: spoken_text is first field in every tool schema
  V2: tool_choice="auto" never "required"
  V3: TTS bypass — tool handler sets spoken_text → push direct to TTS
  V5: 3-layer context (structured state + rolling summary + last 4 raw turns)
  V6: model tiering — gpt-4o-mini standard, gpt-4o on confirmation fail
  V9: STT circuit breaker Deepgram → Groq → Google

Sacred VAD constants (NEVER change):
  confidence=0.6  start_secs=0.2  stop_secs=0.6  min_volume=0.5

The VAD analyzer is built by the *transport* (not as a standalone processor)
because pipecat 0.0.85 wires VAD events through the transport input — see
``build_silero_vad()`` below for the single canonical constructor.
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
import time
from typing import Any, Optional

from pipecat.frames.frames import (
    AudioRawFrame, Frame, LLMMessagesFrame, StartFrame, EndFrame,
    TextFrame, TranscriptionFrame, InterimTranscriptionFrame, TTSSpeakFrame,
    UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
    LLMFullResponseStartFrame, LLMFullResponseEndFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frame_processor import FrameProcessor
from deepgram import LiveOptions
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from core.config import settings
from core.runtime_config import load_voice_settings
from core.ws_manager import ws_manager
from pipeline.context_manager import ConversationContext
from pipeline.dtmf_language_selector import DTMFLanguageSelector, LanguageSelectedFrame
from pipeline.emergency_detector import EmergencyDetector
from pipeline.stt_filter import ConfidenceFilteredSTT
from pipeline.state_machine import ConversationState, ConversationStateMachine
from pipeline.tts_cache import TTSAudioCache
from pipeline.number_converter import replace_numbers_in_text
from providers.language_profile import LanguageProfile, get_profile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sacred VAD parameters — NEVER change these literal values
# ---------------------------------------------------------------------------
_VAD_CONFIDENCE: float = 0.6
_VAD_START_SECS: float = 0.2
_VAD_STOP_SECS: float = 0.6
_VAD_MIN_VOLUME: float = 0.5

# Audio-level broadcast throttle (100 ms)
_AUDIO_LEVEL_INTERVAL_S: float = 0.1

# Spoken greeting per language — the receptionist speaks first on connect.
# Pakistani Urdu / Punjabi (never Indian-Urdu vocabulary).
_GREETINGS: dict[str, str] = {
    "ur-PK": "السلام علیکم، یہ کلینک کی ریسپشن ہے۔ میں آپ کی کیا مدد کر سکتی ہوں؟",
    "pa-PK": "السلام علیکم، کلینک دی ریسپشن توں گل ہو رہی اے۔ میں تہاڈی کی مدد کراں؟",
    "en": "Hello, you've reached the clinic reception. How can I help you today?",
}
_DEFAULT_GREETING = _GREETINGS["en"]


def build_silero_vad() -> SileroVADAnalyzer:
    """Single canonical Silero VAD constructor (sacred literal values).

    Used by every transport — telephony and browser — so all sessions share
    the same VAD behaviour regardless of where audio enters the system.
    """
    return SileroVADAnalyzer(
        params=VADParams(
            confidence=_VAD_CONFIDENCE,
            start_secs=_VAD_START_SECS,
            stop_secs=_VAD_STOP_SECS,
            min_volume=_VAD_MIN_VOLUME,
        )
    )


# ---------------------------------------------------------------------------
# Helper processors
# ---------------------------------------------------------------------------

class AudioLevelMonitor(FrameProcessor):
    """
    Broadcasts mic RMS level to the live call monitor UI at most once per 100 ms.
    All other frame types pass through unchanged.
    """

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self._session_id = session_id
        self._last_broadcast: float = 0.0

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, AudioRawFrame):
            now = time.monotonic()
            if now - self._last_broadcast >= _AUDIO_LEVEL_INTERVAL_S:
                self._last_broadcast = now
                level = self._rms(frame.audio)
                ws_manager.broadcast_fire_and_forget({
                    "type": "call.audio_level",
                    "session_id": self._session_id,
                    "level": round(level, 3),
                })

    @staticmethod
    def _rms(audio: bytes) -> float:
        """Return normalised RMS (0.0–1.0) for 16-bit signed LE PCM."""
        if not audio:
            return 0.0
        try:
            samples = struct.unpack(f"{len(audio) // 2}h", audio[:len(audio) - len(audio) % 2])
        except struct.error:
            return 0.0
        if not samples:
            return 0.0
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        return min(rms / 32768.0, 1.0)


class STTBroadcaster(FrameProcessor):
    """
    Intercepts TranscriptionFrame and broadcasts its content to UI clients.
    Determines partial vs. final from the is_final attribute on the frame.
    All frames (including TranscriptionFrames) pass through downstream unchanged.
    """

    def __init__(self, session_id: str, profile: LanguageProfile) -> None:
        super().__init__()
        self._session_id = session_id
        self._profile = profile

    def set_profile(self, profile: LanguageProfile) -> None:
        self._profile = profile

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        # Surface STT to the browser UI. The LLM context aggregator (downstream)
        # CONSUMES TranscriptionFrame/InterimTranscriptionFrame, so we re-emit
        # the text as a TransportMessageUrgentFrame — the ONLY non-audio frame
        # type the output transport routes through the serializer (a bare
        # SystemFrame is forwarded without serialization, so it never reaches
        # the browser).
        from pipecat.frames.frames import TransportMessageUrgentFrame

        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            logger.info(
                "STT[%s] %s text=%r",
                self._session_id,
                "INTERIM" if isinstance(frame, InterimTranscriptionFrame) else "FINAL",
                (frame.text or "")[:120],
            )

        if isinstance(frame, InterimTranscriptionFrame) and frame.text:
            await self.push_frame(
                TransportMessageUrgentFrame(message={
                    "type": "stt.partial", "text": frame.text, "language": self._profile.code,
                }),
                direction,
            )
        elif isinstance(frame, TranscriptionFrame):
            is_final = getattr(frame, "is_final", True)
            confidence = getattr(frame, "confidence", None)
            ws_manager.broadcast_fire_and_forget({
                "type": "call.stt_final" if is_final else "call.stt_partial",
                "session_id": self._session_id,
                "text": frame.text,
                "language": self._profile.code,
                "is_rtl": self._profile.is_rtl,
                "confidence": round(confidence, 3) if confidence is not None else None,
            })
            if frame.text:
                await self.push_frame(
                    TransportMessageUrgentFrame(message={
                        "type": "stt.final", "text": frame.text, "language": self._profile.code,
                    }),
                    direction,
                )


class ResponseBroadcaster(FrameProcessor):
    """
    Intercepts TextFrame (LLM text output) and broadcasts it to UI clients.
    All frames pass through downstream unchanged.
    """

    def __init__(self, session_id: str, profile: LanguageProfile) -> None:
        super().__init__()
        self._session_id = session_id
        self._profile = profile

    def set_profile(self, profile: LanguageProfile) -> None:
        self._profile = profile

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        from pipecat.frames.frames import TransportMessageUrgentFrame

        if isinstance(frame, TextFrame) and frame.text:
            ws_manager.broadcast_fire_and_forget({
                "type": "call.assistant_response",
                "session_id": self._session_id,
                "text": frame.text,
                "language": self._profile.code,
                "is_rtl": self._profile.is_rtl,
            })
            # Surface the assistant's streamed text to the browser UI. Sent as
            # TransportMessageUrgentFrame so the output transport serializes it
            # (LLMTextFrame/TextFrame are not reliably serialized otherwise).
            await self.push_frame(
                TransportMessageUrgentFrame(message={
                    "type": "llm.text", "text": frame.text,
                }),
                direction,
            )
        elif isinstance(frame, LLMFullResponseEndFrame):
            await self.push_frame(
                TransportMessageUrgentFrame(message={"type": "llm.end"}),
                direction,
            )


class LatencyTracker(FrameProcessor):
    """
    Tracks per-turn latency across three boundaries:
      stt_end         → UserStoppedSpeakingFrame
      llm_first_token → LLMFullResponseStartFrame
      tts_first_chunk → first AudioRawFrame after LLM response starts

    Broadcasts call.latency event after tts_first_chunk. All frames pass through.
    """

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self._session_id = session_id
        self._turn: int = 0
        self._stt_end_ts: Optional[float] = None
        self._llm_start_ts: Optional[float] = None
        self._tts_fired: bool = False

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        now = time.monotonic()

        if isinstance(frame, UserStoppedSpeakingFrame):
            self._turn += 1
            self._stt_end_ts = now
            self._llm_start_ts = None
            self._tts_fired = False

        elif isinstance(frame, LLMFullResponseStartFrame):
            if self._stt_end_ts is not None and self._llm_start_ts is None:
                self._llm_start_ts = now

        elif isinstance(frame, AudioRawFrame):
            if (
                not self._tts_fired
                and self._stt_end_ts is not None
                and self._llm_start_ts is not None
            ):
                self._tts_fired = True
                tts_first_chunk_ts = now
                stt_ms = max(0, int((self._llm_start_ts - self._stt_end_ts) * 1000))
                llm_ms = max(0, int((tts_first_chunk_ts - self._llm_start_ts) * 1000))
                total_ms = max(0, int((tts_first_chunk_ts - self._stt_end_ts) * 1000))
                ws_manager.broadcast_fire_and_forget({
                    "type": "call.latency",
                    "session_id": self._session_id,
                    "turn": self._turn,
                    "stt_ms": stt_ms,
                    "llm_ms": llm_ms,
                    "tts_ms": 0,
                    "total_ms": total_ms,
                })
                logger.debug(
                    "Latency [%s turn %d]: STT→LLM=%dms LLM→TTS=%dms total=%dms",
                    self._session_id, self._turn, stt_ms, llm_ms, total_ms,
                )


class TTSCacheGate(FrameProcessor):
    """
    Checks the TTS audio cache for a TextFrame before it reaches the TTS service.

    Cache HIT:  converts TextFrame → AudioRawFrame, skipping TTS entirely.
    Cache MISS: passes TextFrame through unchanged for the TTS service to handle.

    Number conversion is applied to the text before the cache key lookup so
    keys are consistent with what TTSCacheCapture stores.
    """

    def __init__(
        self,
        cache: TTSAudioCache,
        profile: LanguageProfile,
    ) -> None:
        super().__init__()
        self._cache = cache
        self._profile = profile

    def set_profile(self, profile: LanguageProfile) -> None:
        self._profile = profile

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and frame.text:
            converted = replace_numbers_in_text(frame.text, self._profile.number_converter)
            cached = self._cache.get(self._profile.code, converted)
            if cached is not None:
                logger.debug(
                    "TTSCacheGate: HIT [%s] %r (%d bytes)",
                    self._profile.code, converted[:40], len(cached),
                )
                await self.push_frame(
                    # Cached audio is stored straight from the TTS service, which
                    # synthesises at tts_out_rate (24 kHz — see start()). Tagging
                    # the frame 16 kHz made the browser build the playback buffer
                    # at the wrong rate, so cache hits (greeting + repeated
                    # phrases) played at half speed / were effectively inaudible.
                    AudioRawFrame(audio=cached, sample_rate=24000, num_channels=1),
                    direction,
                )
                return
        await self.push_frame(frame, direction)


class TTSCacheCapture(FrameProcessor):
    """
    Captures synthesised audio from the TTS service and stores it in the cache
    keyed against the most recently seen TextFrame text.

    Capture window: from the first AudioRawFrame after a TextFrame until the next
    TextFrame, UserStartedSpeakingFrame, or EndFrame resets the accumulator.

    Minimum 1 KB of audio is required before storing (avoids caching silence).
    """

    _MIN_CACHE_BYTES: int = 1024

    def __init__(self, cache: TTSAudioCache, profile: LanguageProfile) -> None:
        super().__init__()
        self._cache = cache
        self._profile = profile
        self._pending_text: Optional[str] = None
        self._audio_buf: bytearray = bytearray()

    def set_profile(self, profile: LanguageProfile) -> None:
        self._flush()
        self._profile = profile

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and frame.text:
            self._flush()
            self._pending_text = replace_numbers_in_text(
                frame.text, self._profile.number_converter
            )
            self._audio_buf = bytearray()

        elif isinstance(frame, AudioRawFrame) and self._pending_text:
            self._audio_buf.extend(frame.audio)

        elif isinstance(frame, (EndFrame, UserStartedSpeakingFrame)):
            self._flush()

        await self.push_frame(frame, direction)

    def _flush(self) -> None:
        if self._pending_text and len(self._audio_buf) >= self._MIN_CACHE_BYTES:
            self._cache.put(
                self._profile.code,
                self._pending_text,
                bytes(self._audio_buf),
            )
            logger.debug(
                "TTSCacheCapture: stored [%s] %r (%d bytes)",
                self._profile.code, self._pending_text[:40], len(self._audio_buf),
            )
        self._pending_text = None
        self._audio_buf = bytearray()


# ---------------------------------------------------------------------------
# Azure TTS FrameProcessor (pipecat 0.0.85 has no built-in Azure TTS service)
# ---------------------------------------------------------------------------

class _AzureTTSProcessor(FrameProcessor):
    """
    Minimal FrameProcessor that synthesises TextFrames via AzureNeuralTTSAdapter
    and emits AudioRawFrame downstream.  Lazy-initialises the adapter so Azure SDK
    is only imported if this processor is actually used.
    """

    def __init__(self, language: str, voice: str) -> None:
        super().__init__()
        self._language = language
        self._voice = voice
        self._adapter = None

    def _get_adapter(self):
        if self._adapter is None:
            from providers.tts.azure_neural import AzureNeuralTTSAdapter
            self._adapter = AzureNeuralTTSAdapter(
                language=self._language,
                voice=self._voice,
            )
        return self._adapter

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and frame.text:
            try:
                result = await self._get_adapter().synthesize(frame.text)
                await self.push_frame(
                    AudioRawFrame(
                        audio=result.audio_bytes,
                        sample_rate=16000,
                        num_channels=1,
                    ),
                    direction,
                )
                return
            except Exception as exc:
                logger.error(
                    "_AzureTTSProcessor: synthesis failed for %r: %s",
                    frame.text[:40], exc,
                )
        await self.push_frame(frame, direction)


class _ElevenLabsTTSProcessor(FrameProcessor):
    """
    Pipecat FrameProcessor wrapping our ElevenLabs adapter so the PSTN/pipecat
    pipeline can use ElevenLabs voices when admin selects tts_provider=elevenlabs.
    Reads voice_id + model_id at construction time from runtime settings.
    """

    def __init__(self, language: str, voice: str, model_id: str) -> None:
        super().__init__()
        self._language = language
        self._voice = voice
        self._model_id = model_id
        self._adapter = None

    def _get_adapter(self):
        if self._adapter is None:
            from providers.tts.elevenlabs import ElevenLabsTTSAdapter
            self._adapter = ElevenLabsTTSAdapter(
                language=self._language,
                voice=self._voice,
                model_id=self._model_id,
            )
        return self._adapter

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and frame.text:
            try:
                result = await self._get_adapter().synthesize(frame.text)
                await self.push_frame(
                    AudioRawFrame(
                        audio=result.audio_bytes,
                        sample_rate=16000,
                        num_channels=1,
                    ),
                    direction,
                )
                return
            except Exception as exc:
                logger.error(
                    "_ElevenLabsTTSProcessor: synthesis failed for %r: %s",
                    frame.text[:40], exc,
                )
        await self.push_frame(frame, direction)


# ---------------------------------------------------------------------------
# Main pipeline class
# ---------------------------------------------------------------------------

class ReceptionistPipeline:
    """
    One instance per active session — telephony call OR browser test session.

    The pipeline body is identical for both; only the ``transport`` object
    passed in differs (its input()/output() processors are what bridge to
    the actual WebSocket). The transport carries its own serializer
    (Twilio/Plivo for telephony, BrowserPCMSerializer for browser) and its
    own VAD analyzer.

    Call lifecycle:
      1. Telephony webhook OR browser WS endpoint constructs this instance
         with session_id + transport.
      2. start() assembles the Pipecat 0.0.85 pipeline and runs it.
      3. DTMFLanguageSelector arms a timer on StartFrame; the caller delivers
         digits via ``handle_dtmf(digit)`` (telephony DTMF webhook OR browser
         control message).
      4. Pipeline processes audio until the session ends or an emergency
         is detected.
      5. stop() publishes post-call analytics event.
    """

    def __init__(
        self,
        session_id: str,
        transport: Any,
        language: str = "ur-PK",
        stt_provider: Optional[str] = None,
        llm_provider: Optional[str] = None,
        tts_provider: Optional[str] = None,
        preselect_language: bool = False,
    ) -> None:
        if transport is None:
            raise ValueError("ReceptionistPipeline requires a transport")
        self._session_id = session_id
        self._transport = transport
        self._language = language
        # Browser sessions choose language up front (dropdown) → skip the 5s
        # IVR DTMF wait. Telephony leaves this False to run the IVR prompt.
        self._preselect_language = preselect_language
        # Per-session provider overrides chosen on the Test Session page (or
        # passed by telephony). None → fall back to the language profile /
        # admin runtime_config defaults. Normalised to lowercase for matching.
        self._stt_provider = (stt_provider or "").lower().strip() or None
        self._llm_provider = (llm_provider or "").lower().strip() or None
        self._tts_provider = (tts_provider or "").lower().strip() or None
        self._profile: LanguageProfile = get_profile(language)
        self._state_machine = ConversationStateMachine(session_id, language)
        self._emergency_detector = EmergencyDetector()
        self._context = ConversationContext(session_id, language)
        self._tts_cache = TTSAudioCache(
            cache_dir=settings.tts_cache_dir,
            max_size_mb=settings.tts_cache_max_mb,
        )
        self._dtmf_selector = DTMFLanguageSelector(
            # When preselected, default to THIS session's chosen language so
            # the immediate LanguageSelectedFrame matches the browser choice.
            default_language=language if preselect_language else settings.default_language,
            timeout_secs=5.0,
            preselected=preselect_language,
        )
        self._started_at: float = 0.0
        self._task: Optional[PipelineTask] = None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def dtmf_selector(self) -> DTMFLanguageSelector:
        """External callers (telephony webhook, browser control msg handler)
        deliver digits through this selector's ``handle_dtmf(digit)``."""
        return self._dtmf_selector

    @property
    def task(self) -> Optional[PipelineTask]:
        return self._task

    # ------------------------------------------------------------------
    # Provider builders — per-session override > profile default
    # ------------------------------------------------------------------

    def _build_stt(self):
        """Construct the STT service for this session.

        Selection precedence: explicit ``stt_provider`` override (chosen on
        the Test Session page) > the language profile's default provider.

        Supported providers:
          deepgram        — streaming WebSocket (lowest latency; en/ur)
          openai_whisper  — OpenAI Whisper, batch via VAD segmentation
          groq_whisper    — Groq Whisper-large-v3 (default for pa-PK)
        """
        provider = self._stt_provider or self._profile.stt.provider
        provider = provider.lower()

        if provider in ("openai_whisper", "openai", "whisper"):
            from pipecat.services.openai.stt import OpenAISTTService
            if not settings.openai_api_key:
                raise RuntimeError("OPENAI_API_KEY not set — required for OpenAI Whisper STT")
            # Whisper auto-detects language; passing it is optional and the
            # pipecat service expects a Language enum, not our string code —
            # so we omit it and let Whisper detect (better for code-switching).
            logger.info("pipeline stt: openai_whisper (model=whisper-1)")
            return OpenAISTTService(
                api_key=settings.openai_api_key,
                model="whisper-1",
            )

        if provider in ("groq_whisper", "groq"):
            from pipecat.services.groq.stt import GroqSTTService
            if not settings.groq_api_key:
                raise RuntimeError("GROQ_API_KEY not set — required for Groq Whisper STT")
            logger.info("pipeline stt: groq_whisper (model=whisper-large-v3)")
            return GroqSTTService(
                api_key=settings.groq_api_key,
                model="whisper-large-v3",
            )

        # Default: Deepgram streaming.
        if not settings.deepgram_api_key:
            raise RuntimeError("DEEPGRAM_API_KEY not set — required for Deepgram STT")
        logger.info(
            "pipeline stt: deepgram (lang=%s model=%s)",
            self._profile.stt.language_code, self._profile.stt.model or "nova-2",
        )
        # pipecat 0.0.85's DeepgramSTTService takes Deepgram params via a
        # single live_options=LiveOptions(...) kwarg — everything else flows
        # into **kwargs and is silently dropped. Without these, Deepgram runs
        # with SDK defaults (no endpointing, vad_events=False), so it only
        # emits InterimTranscriptionFrames and never the TranscriptionFrame
        # ConfidenceFilteredSTT looks for — the bot then never replies.
        return DeepgramSTTService(
            api_key=settings.deepgram_api_key,
            live_options=LiveOptions(
                language=self._profile.stt.language_code,
                model=self._profile.stt.model or "nova-2",
                endpointing=self._profile.stt.endpointing_ms,
                utterance_end_ms=str(self._profile.stt.utterance_end_ms),
                no_delay=True,
                vad_events=True,
                smart_format=True,
                punctuate=True,
                interim_results=True,
                encoding="linear16",
                channels=1,
            ),
        )

    def _build_llm(self):
        """Construct the LLM service for this session.

        Selection precedence: explicit ``llm_provider`` override > OpenAI.
        tool_choice="auto" is enforced downstream regardless of provider.
        """
        provider = (self._llm_provider or "openai").lower()

        if provider == "groq":
            from pipecat.services.groq.llm import GroqLLMService
            if not settings.groq_api_key:
                raise RuntimeError("GROQ_API_KEY not set — required for Groq LLM")
            logger.info("pipeline llm: groq (llama-3.3-70b-versatile)")
            return GroqLLMService(
                api_key=settings.groq_api_key,
                model="llama-3.3-70b-versatile",
            )

        if provider == "anthropic":
            from pipecat.services.anthropic.llm import AnthropicLLMService
            if not getattr(settings, "anthropic_api_key", ""):
                raise RuntimeError("ANTHROPIC_API_KEY not set — required for Anthropic LLM")
            logger.info("pipeline llm: anthropic (claude-haiku)")
            return AnthropicLLMService(
                api_key=settings.anthropic_api_key,
                model="claude-3-5-haiku-latest",
            )

        # Default: OpenAI.
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY not set — required for OpenAI LLM")
        logger.info("pipeline llm: openai (model=%s)", settings.llm_model_standard)
        return OpenAILLMService(
            api_key=settings.openai_api_key,
            model=settings.llm_model_standard,
        )

    async def start(self) -> None:
        self._started_at = time.monotonic()
        self._state_machine.transition(ConversationState.LANGUAGE_SELECT)

        ws_manager.broadcast_fire_and_forget({
            "type": "call.started",
            "session_id": self._session_id,
            "language": self._language,
        })

        # VAD lives on the transport (set by the caller — see telephony /
        # browser endpoints) so the pipeline body is transport-agnostic.

        # -- STT — per-session override wins, else language-profile default --
        stt = self._build_stt()

        # -- Confidence filter -----------------------------------------------
        stt_filter = ConfidenceFilteredSTT(
            confidence_threshold=self._profile.stt.confidence_threshold,
            noise_words=self._profile.noise_words,
        )
        stt_filter.set_language_profile(self._profile)

        # -- Monitoring processors ------------------------------------------
        audio_monitor = AudioLevelMonitor(self._session_id)
        stt_broadcaster = STTBroadcaster(self._session_id, self._profile)
        response_broadcaster = ResponseBroadcaster(self._session_id, self._profile)
        latency_tracker = LatencyTracker(self._session_id)

        # -- LLM — per-session override wins, else OpenAI default ----------
        llm = self._build_llm()

        # -- TTS cache gate + capture ----------------------------------------
        tts_cache_gate = TTSCacheGate(cache=self._tts_cache, profile=self._profile)
        tts_cache_capture = TTSCacheCapture(cache=self._tts_cache, profile=self._profile)

        # -- TTS service — precedence:
        #      per-session override > admin runtime_config > profile default.
        voice_settings = await load_voice_settings()
        # Default the receptionist to female unless overridden by call metadata.
        gender = "female"

        # Resolve the effective TTS provider. Normalise OpenAI variants.
        session_tts = self._tts_provider
        if session_tts in ("openai_tts",):
            session_tts = "openai"
        admin_provider = (voice_settings.tts_provider or "").lower()
        chosen_tts = session_tts or admin_provider
        if chosen_tts == "elevenlabs":
            tts_provider_used = "elevenlabs"
        elif chosen_tts == "azure":
            tts_provider_used = "azure"
        elif chosen_tts == "openai":
            tts_provider_used = "openai"
        else:
            tts_provider_used = self._profile.tts.provider  # fall back to profile
        logger.info(
            "pipeline tts provider resolved=%s (session=%s admin=%s profile=%s)",
            tts_provider_used, session_tts, admin_provider, self._profile.tts.provider,
        )

        # Use pipecat's NATIVE streaming TTS services (not hand-rolled batch
        # processors): they handle TTSSpeakFrame (the greeting), sentence
        # aggregation, and streaming — the custom processors did none of these,
        # which is why ElevenLabs/Azure produced no greeting audio.
        tts_out_rate = 24000  # match transport audio_out_sample_rate
        if tts_provider_used == "elevenlabs":
            from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
            # CRITICAL: use a real ElevenLabs voice_id. voice_for_gender() only
            # returns an ElevenLabs id when the *admin* tts_provider is
            # elevenlabs; otherwise it returns an OpenAI voice name (e.g.
            # "shimmer") which ElevenLabs rejects with 1008. Resolve the
            # ElevenLabs voice directly here.
            voice_id = (
                voice_settings.elevenlabs_custom_voice_id
                or (voice_settings.elevenlabs_male_voice_id if gender == "male"
                    else voice_settings.elevenlabs_female_voice_id)
            )
            tts = ElevenLabsTTSService(
                api_key=settings.elevenlabs_api_key,
                voice_id=voice_id,
                model=voice_settings.elevenlabs_model_id,  # eleven_flash_v2_5 (sacred)
                sample_rate=tts_out_rate,
            )
            logger.info(
                "pipeline tts: elevenlabs(native) voice=%s model=%s",
                voice_id, voice_settings.elevenlabs_model_id,
            )
        elif tts_provider_used == "azure":
            from pipecat.services.azure.tts import AzureTTSService
            tts = AzureTTSService(
                api_key=settings.azure_speech_key,
                region=settings.azure_speech_region,
                voice=self._profile.tts.voice,
                sample_rate=tts_out_rate,
            )
            logger.info("pipeline tts: azure(native) voice=%s", self._profile.tts.voice)
        else:
            # OpenAI TTS — use the OpenAI-realtime-style voice from settings.
            openai_voice = voice_settings.openai_realtime_male_voice if gender == "male" else voice_settings.openai_realtime_female_voice
            tts = OpenAITTSService(
                api_key=settings.openai_api_key,
                voice=openai_voice or self._profile.tts.voice or "nova",
                sample_rate=tts_out_rate,
            )
            logger.info("pipeline tts: openai voice=%s", openai_voice)

        # -- LLM context + aggregators --------------------------------------
        #
        # CRITICAL: an OpenAILLMService does NOT run on a bare TranscriptionFrame.
        # The user-side context aggregator turns finalised transcripts into an
        # OpenAILLMContextFrame that triggers inference; the assistant-side
        # aggregator captures the reply back into the running context so the
        # conversation has memory across turns.
        clinic_name = await self._get_clinic_name()
        # Tools: register the full clinic booking suite so the bot performs REAL
        # actions (search doctors, find slots, book, reschedule, cancel, triage)
        # instead of hallucinating bookings. Uses the rich tool-aware "Amina"
        # prompt. Falls back to the plain greeting prompt if tools are unavailable.
        from pipeline.booking_tools import (
            booking_tools_schema, booking_system_prompt, register_booking_tools,
        )
        try:
            system_prompt = booking_system_prompt(self._language)
            tools = booking_tools_schema()
        except Exception as exc:
            logger.warning("booking tools/prompt unavailable (%s); falling back", exc)
            system_prompt = self._load_system_prompt(clinic_name)
            tools = None

        # Seed the greeting as the assistant's first turn so the LLM knows it
        # already greeted — otherwise the system prompt's "greet first" rule
        # makes it greet AGAIN on the user's opening turn (double greeting).
        greeting = _GREETINGS.get(self._language, _DEFAULT_GREETING)
        context = OpenAILLMContext(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "assistant", "content": greeting},
            ],
            tools=tools if tools else [],
        )
        context_aggregator = llm.create_context_aggregator(context)

        if tools:
            register_booking_tools(llm, self._session_id)

        # -- Assemble pipeline in correct processor order -------------------
        #
        # transport.input() emits InputAudioRawFrame; the transport's own
        # VAD analyzer (set at construction time) produces UserStartedSpeaking
        # / UserStoppedSpeaking frames in line with the audio.
        #
        # DTMFLanguageSelector sits early so it can buffer frames until the
        # language is chosen and emit LanguageSelectedFrame downstream.
        # context_aggregator.user() sits right before the LLM so transcripts
        # trigger inference; context_aggregator.assistant() sits at the very
        # end so the assistant reply (after TTS) is folded back into context.
        # tts_cache_gate sits before TTS so cache hits bypass synthesis.
        # transport.output() consumes audio + serializable text frames back to
        # the WebSocket (Twilio/Plivo serializer drops text; browser emits it).
        #
        pipeline = Pipeline([
            self._transport.input(),       # 0. WS → InputAudioRawFrame (+ VAD)
            audio_monitor,                 # 1. RMS broadcast (throttled)
            self._dtmf_selector,           # 2. Buffer until language selected
            stt,                           # 3. Speech-to-text
            stt_filter,                    # 4. Drop low-confidence / noise
            stt_broadcaster,               # 5. Broadcast accepted transcripts
            context_aggregator.user(),     # 6. Transcript → LLM context (triggers run)
            llm,                           # 7. LLM inference (tool_choice="auto")
            response_broadcaster,          # 8. Broadcast LLM text to UI
            tts_cache_gate,                # 9. Serve from TTS cache if available
            tts,                           # 10. TTS synthesis (cache misses only)
            tts_cache_capture,             # 11. Store new synthesis in cache
            latency_tracker,               # 12. Measure and broadcast latency
            self._transport.output(),      # 13. Serialize audio + events to WS
            context_aggregator.assistant(),# 14. Fold reply back into context
        ])

        self._task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,  # SACRED — never False
                enable_metrics=True,
            ),
        )

        # Speak the greeting as soon as the client's audio channel is up.
        greeting = _GREETINGS.get(self._language, _DEFAULT_GREETING)

        @self._transport.event_handler("on_client_connected")
        async def _on_client_connected(transport, client):  # noqa: ANN001
            logger.info("session %s: client connected — sending greeting", self._session_id)
            # Surface the greeting *text* to the UI as well. The WebRTC data
            # channel (and the WS serializer) only forward TransportMessage*
            # frames, and the greeting is spoken via TTSSpeakFrame which never
            # produces an LLMTextFrame — so without this the greeting plays as
            # audio but never appears in the transcript panel.
            from pipecat.frames.frames import TransportMessageUrgentFrame
            await self._task.queue_frames([
                TransportMessageUrgentFrame(message={"type": "bot.text", "text": greeting}),
                TTSSpeakFrame(greeting),
            ])

        runner = PipelineRunner()
        await runner.run(self._task)

    def _load_system_prompt(self, clinic_name: str = "the clinic") -> str:
        """
        Load the language-appropriate system prompt from prompts YAML.
        Falls back to a minimal inline prompt if the file is unavailable.
        Substitutes [CLINIC_NAME] so the placeholder never reaches speech.
        """
        import yaml
        variant = self._profile.llm_prompt_variant
        prompt_path = f"prompts/{variant}/greeting_{variant}.yaml"
        try:
            with open(prompt_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            prompt = data.get("system_prompt", "")
        except (FileNotFoundError, KeyError, Exception):
            prompt = f"You are a medical receptionist. Respond in {self._profile.code}."
        return prompt.replace("[CLINIC_NAME]", clinic_name)

    async def _get_clinic_name(self) -> str:
        """Fetch the clinic display name from the DB; fall back gracefully."""
        try:
            from sqlalchemy import select
            from models.clinic import ClinicConfig
            from core.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                clinic = await db.scalar(select(ClinicConfig).limit(1))
                if clinic and clinic.clinic_name:
                    return clinic.clinic_name
        except Exception as exc:
            logger.debug("clinic name lookup failed: %s", exc)
        return "the clinic"

    async def handle_transcript(
        self,
        text: str,
        confidence: float,
        is_final: bool,
    ) -> None:
        """
        Process a finalised transcript.

        1. Emergency detection runs first (pre-LLM, < 10 s target).
        2. Non-emergency final transcripts are added to the 3-layer context.
        3. Partial transcripts are broadcast only (not sent to LLM).
        """
        if not is_final:
            ws_manager.broadcast_fire_and_forget({
                "type": "call.stt_partial",
                "session_id": self._session_id,
                "text": text,
                "language": self._language,
                "confidence": round(confidence, 3),
            })
            return

        if not text or not text.strip():
            return

        # Emergency detection — pre-LLM keyword scan
        if self._emergency_detector.detect(text, self._language):
            matched = self._emergency_detector.get_matched_keywords(text, self._language)
            logger.warning(
                "EMERGENCY detected [%s]: keywords=%s",
                self._session_id,
                matched,  # audit log only — never exposed to UI or TTS
            )
            await self._handle_emergency()
            return

        # Update 3-layer context with caller turn
        self._context.add_turn("user", text)
        self._context.update_state(language=self._language)

        # Build messages and inject into LLM via queue
        if self._task:
            system_prompt = self._load_system_prompt()
            messages = self._context.get_messages_for_llm(system_prompt)
            await self._task.queue_frame(LLMMessagesFrame(messages))

    async def _handle_emergency(self) -> None:
        """
        Immediately transition to EMERGENCY state, broadcast the event,
        attempt warm transfer to triage nurse, and stop the pipeline.
        """
        self._state_machine.transition(ConversationState.EMERGENCY)

        ws_manager.broadcast_fire_and_forget({
            "type": "call.emergency",
            "session_id": self._session_id,
            "language": self._language,
        })

        logger.warning(
            "ReceptionistPipeline [%s]: EMERGENCY — initiating transfer",
            self._session_id,
        )

        # Fetch triage number (env var first, then DB)
        triage_number = self._get_triage_number_from_env()
        if not triage_number:
            triage_number = await self._get_triage_number_from_db()

        if triage_number:
            try:
                from providers.base import CallTransfer
                from providers.registry import registry
                transfer = CallTransfer(
                    target_number=triage_number,
                    caller_number="",
                    session_id=self._session_id,
                    context={"reason": "emergency", "language": self._language},
                )
                telephony = registry.make_telephony(settings.telephony_primary)
                await telephony.transfer_call(transfer)
                logger.info(
                    "ReceptionistPipeline [%s]: emergency transfer initiated → %s",
                    self._session_id, triage_number,
                )
            except Exception as exc:
                logger.error(
                    "ReceptionistPipeline [%s]: emergency transfer failed: %s",
                    self._session_id, exc,
                )
        else:
            logger.error(
                "ReceptionistPipeline [%s]: no triage number configured — "
                "emergency transfer skipped",
                self._session_id,
            )

        await self.stop()

    @staticmethod
    def _get_triage_number_from_env() -> Optional[str]:
        number = os.environ.get("TRIAGE_NURSE_NUMBER", "").strip()
        return number if number else None

    async def _get_triage_number_from_db(self) -> Optional[str]:
        """Non-critical DB lookup — returns None on any error."""
        try:
            from sqlalchemy import select
            from models.clinic import ClinicConfig
            from core.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                clinic = await db.scalar(select(ClinicConfig).limit(1))
                return clinic.triage_nurse_number if clinic else None
        except Exception as exc:
            logger.warning(
                "ReceptionistPipeline [%s]: DB triage number lookup failed: %s",
                self._session_id, exc,
            )
            return None

    async def stop(self) -> None:
        """
        Gracefully terminate the pipeline. Idempotent — safe to call multiple times.
        Publishes post-call analytics event before cancelling the task.
        """
        duration = time.monotonic() - self._started_at if self._started_at else 0.0

        # Transition to a terminal state if not already there
        if not self._state_machine.is_terminal():
            self._state_machine.transition(ConversationState.GOODBYE)

        if self._task:
            try:
                await self._task.cancel()
            except Exception as exc:
                logger.debug(
                    "ReceptionistPipeline [%s]: task cancel: %s", self._session_id, exc
                )
            self._task = None

        state = self._context.get_state()
        ws_manager.broadcast_fire_and_forget({
            "type": "call.ended",
            "session_id": self._session_id,
            "duration_seconds": int(duration),
            "language": self._language,
            "turn_count": state.turn_count,
            "booking_confirmed": state.booking_confirmed,
            "final_state": self._state_machine.current_state.value,
        })

        logger.info(
            "ReceptionistPipeline [%s]: stopped (duration=%.1fs turns=%d state=%s)",
            self._session_id,
            duration,
            state.turn_count,
            self._state_machine.current_state.value,
        )
