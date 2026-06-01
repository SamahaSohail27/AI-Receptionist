"""
BrowserPCMSerializer — pipecat FrameSerializer for the browser test-session
WebSocket transport.

Why TEXT mode (not BINARY)?
---------------------------
pipecat 0.0.85's FastAPIWebsocketClient locks each connection to either
``iter_bytes()`` or ``iter_text()`` based on ``serializer.type`` — it cannot
mix binary and text frames on the same socket. For the browser we need both
PCM audio and JSON events, so we run the socket in TEXT mode and base64-
encode the audio inside a JSON envelope. The bandwidth overhead is ~33%,
which is irrelevant for browser test sessions but lets a single pipecat
transport carry everything (audio + STT + LLM + control) with no parallel
channels and no subclassing of pipecat internals.

Telephony pipelines keep their native BINARY serializers (Twilio / Plivo) —
this serializer is only used by ``/test-session/ws/{sid}``.

Wire format
-----------
Every message is a JSON object with a ``type`` field.

Inbound  (browser → server):
    {"type": "audio",  "data": "<base64 PCM16 mono>"}
    {"type": "dtmf",   "digit": "1" | "2" | "3"}
    {"type": "hangup"}

Outbound (server → browser):
    {"type": "audio",          "data": "<base64 PCM16 mono>", "sample_rate": 24000}
    {"type": "stt.partial",    "text": "...", "language": "ur-PK"}
    {"type": "stt.final",      "text": "...", "language": "ur-PK"}
    {"type": "llm.text",       "text": "..."}
    {"type": "llm.end"}

Other frame types (system / lifecycle) are silently dropped.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Optional

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    StartFrame,
    SystemFrame,
    TranscriptionFrame,
    TransportMessageFrame,
    TransportMessageUrgentFrame,
    TTSAudioRawFrame,
    TTSTextFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer, FrameSerializerType

logger = logging.getLogger(__name__)


@dataclass
class BrowserControlFrame(SystemFrame):
    """Control frame produced by the serializer from inbound JSON messages.

    The browser test-session WebSocket endpoint inspects these frames (or
    routes them to the appropriate sub-component) — e.g. a ``dtmf`` payload
    triggers ``DTMFLanguageSelector.handle_dtmf(digit)`` and a ``hangup``
    payload ends the pipeline.
    """
    payload: dict


@dataclass
class BrowserUIFrame(SystemFrame):
    """UI event pushed downstream so it reaches the output serializer.

    Why a SystemFrame? The LLM context aggregator *consumes* TranscriptionFrame
    / InterimTranscriptionFrame, so those never reach transport.output(). A
    SystemFrame passes through every processor untouched, so broadcasters
    sitting *before* the aggregator can surface STT/LLM events to the browser
    by emitting one of these. The serializer turns ``event`` into the WS JSON.
    """
    event: dict


class BrowserPCMSerializer(FrameSerializer):
    """JSON-envelope serializer for the browser WS transport (text mode)."""

    def __init__(self, sample_rate: int = 24000) -> None:
        super().__init__()
        self._sample_rate = sample_rate
        self._language: str = "ur-PK"

    @property
    def type(self) -> FrameSerializerType:
        return FrameSerializerType.TEXT

    async def setup(self, frame: StartFrame) -> None:
        # Honour the negotiated audio sample rate so outbound audio metadata
        # matches what the browser AudioContext is decoding at.
        if getattr(frame, "audio_out_sample_rate", None):
            self._sample_rate = frame.audio_out_sample_rate

    def set_language(self, language: str) -> None:
        """Called by the pipeline after DTMFLanguageSelector picks a profile."""
        self._language = language

    # ----------------------------------------------------------- serialize ←
    async def serialize(self, frame: Frame) -> Optional[str]:
        if isinstance(frame, (OutputAudioRawFrame, TTSAudioRawFrame)):
            return json.dumps({
                "type": "audio",
                "data": base64.b64encode(frame.audio).decode("ascii"),
                "sample_rate": frame.sample_rate or self._sample_rate,
            })

        if isinstance(frame, InterimTranscriptionFrame):
            return json.dumps({
                "type": "stt.partial",
                "text": frame.text or "",
                "language": self._language,
            })
        if isinstance(frame, TranscriptionFrame):
            return json.dumps({
                "type": "stt.final",
                "text": frame.text or "",
                "language": self._language,
            })

        if isinstance(frame, LLMTextFrame):
            return json.dumps({"type": "llm.text", "text": frame.text or ""})
        if isinstance(frame, LLMFullResponseEndFrame):
            return json.dumps({"type": "llm.end"})

        # TTSTextFrame is the text the TTS engine is actually speaking — this
        # is what surfaces the *greeting* (sent via TTSSpeakFrame, which never
        # produces an LLMTextFrame) and any direct spoken text in the UI.
        if isinstance(frame, TTSTextFrame):
            return json.dumps({"type": "bot.text", "text": frame.text or ""})

        # UI events forwarded from broadcasters before the LLM aggregator
        # (e.g. user STT transcripts the aggregator would otherwise consume).
        # These travel as TransportMessageUrgentFrame because a bare SystemFrame
        # is NOT serialized by the output transport — only TransportMessage*
        # frames are routed through send_message() → serialize().
        if isinstance(frame, (TransportMessageFrame, TransportMessageUrgentFrame)):
            return json.dumps(frame.message)
        if isinstance(frame, BrowserUIFrame):
            return json.dumps(frame.event)

        # Surface pipeline errors to the browser so failures are visible
        # instead of a silent stall.
        if isinstance(frame, ErrorFrame):
            return json.dumps({
                "type": "error",
                "error": {"message": str(getattr(frame, "error", "") or frame)},
            })

        return None

    # --------------------------------------------------------- deserialize →
    async def deserialize(self, data: str | bytes) -> Optional[Frame]:
        if isinstance(data, (bytes, bytearray, memoryview)):
            # Shouldn't happen in TEXT mode but be defensive.
            try:
                data = bytes(data).decode("utf-8")
            except UnicodeDecodeError:
                return None

        try:
            obj = json.loads(data)
        except (ValueError, TypeError):
            logger.warning("BrowserPCMSerializer: dropping non-JSON frame")
            return None

        if not isinstance(obj, dict):
            return None

        msg_type = obj.get("type")

        if msg_type == "audio":
            try:
                audio = base64.b64decode(obj.get("data", ""))
            except (ValueError, TypeError):
                return None
            if not audio:
                return None
            return InputAudioRawFrame(
                audio=audio,
                sample_rate=int(obj.get("sample_rate") or self._sample_rate),
                num_channels=1,
            )

        if msg_type in ("dtmf", "hangup"):
            return BrowserControlFrame(payload=obj)

        logger.debug("BrowserPCMSerializer: unhandled control type %r", msg_type)
        return None
