"""
Abstract base classes for all provider adapters.
Every concrete provider must subclass the appropriate base and implement all abstract methods.
"""
from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator, Any


# ---------------------------------------------------------------------------
# Shared data structures
# ---------------------------------------------------------------------------

@dataclass
class STTResult:
    text: str
    confidence: float
    language: str
    is_final: bool
    provider: str
    latency_ms: int = 0


@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cached_tokens: int = 0


@dataclass
class TTSResult:
    audio_bytes: bytes
    provider: str
    voice: str
    language: str
    latency_ms: int = 0
    from_cache: bool = False


@dataclass
class CallTransfer:
    target_number: str
    caller_number: str
    session_id: str
    context: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# STT Base
# ---------------------------------------------------------------------------

class BaseSTT(abc.ABC):
    """Base class for all Speech-to-Text adapters.

    Adapters must use WebSocket streaming — no HTTP request-response.
    """

    def __init__(self, language: str, confidence_threshold: float) -> None:
        self.language = language
        self.confidence_threshold = confidence_threshold

    @abc.abstractmethod
    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[STTResult]:
        """Yield STTResult for each utterance (partial + final)."""
        ...

    @abc.abstractmethod
    async def set_language(self, language_code: str) -> None:
        """Hot-swap language without restarting the connection."""
        ...

    def get_confidence_threshold(self) -> float:
        return self.confidence_threshold

    @property
    @abc.abstractmethod
    def provider_name(self) -> str: ...


# ---------------------------------------------------------------------------
# LLM Base
# ---------------------------------------------------------------------------

class BaseLLM(abc.ABC):
    """Base class for all LLM adapters.

    All adapters must support:
    - tool_choice="auto" (never "required") — saves 300-700ms on simple turns
    - prompt_caching where available
    - model_tier: "fast" | "quality"
    """

    def __init__(self, model_tier: str = "fast") -> None:
        self.model_tier = model_tier  # "fast" | "quality"

    @abc.abstractmethod
    async def stream_response(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[str | LLMResponse]:
        """Stream tokens; yield str for partial tokens, LLMResponse at end."""
        ...

    @abc.abstractmethod
    def set_language_profile(self, language_code: str, system_prompt: str) -> None:
        """Inject language-specific system prompt for next call."""
        ...

    @abc.abstractmethod
    def cache_prefix(self) -> str | None:
        """Return the cacheable static prefix of the system prompt, or None."""
        ...

    @property
    @abc.abstractmethod
    def provider_name(self) -> str: ...

    @property
    @abc.abstractmethod
    def model_name(self) -> str: ...


# ---------------------------------------------------------------------------
# TTS Base
# ---------------------------------------------------------------------------

class BaseTTS(abc.ABC):
    """Base class for all Text-to-Speech adapters."""

    def __init__(self, language: str, voice: str) -> None:
        self.language = language
        self.voice = voice

    @abc.abstractmethod
    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Stream audio bytes as they are generated."""
        ...

    @abc.abstractmethod
    async def synthesize(self, text: str) -> TTSResult:
        """Return full audio for caching."""
        ...

    @abc.abstractmethod
    async def set_language(self, language_code: str, voice: str) -> None:
        """Switch language and voice without recreating client."""
        ...

    @property
    @abc.abstractmethod
    def provider_name(self) -> str: ...


# ---------------------------------------------------------------------------
# Telephony Base
# ---------------------------------------------------------------------------

class BaseTelephony(abc.ABC):
    """Base class for all telephony adapters (Plivo, Twilio, Telnyx)."""

    @abc.abstractmethod
    async def handle_inbound(self, webhook_data: dict) -> dict:
        """Parse inbound call webhook. Return normalized call metadata."""
        ...

    @abc.abstractmethod
    async def handle_dtmf(self, session_id: str, digit: str) -> None:
        """Handle DTMF digit received during call."""
        ...

    @abc.abstractmethod
    async def transfer_call(self, transfer: CallTransfer) -> bool:
        """Warm transfer to human. Returns True if successful."""
        ...

    @abc.abstractmethod
    async def end_call(self, session_id: str, reason: str = "completed") -> None:
        """Gracefully end the call."""
        ...

    @abc.abstractmethod
    async def send_dtmf_prompt(
        self, session_id: str, audio_url: str
    ) -> None:
        """Play DTMF language-selection prompt to caller."""
        ...

    @property
    @abc.abstractmethod
    def provider_name(self) -> str: ...
