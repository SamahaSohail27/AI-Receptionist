"""
Groq Whisper STT adapter — batch transcription via groq SDK.

Primary provider for Punjabi (pa-PK); also serves as Urdu/English fallback.
Groq Whisper is not true streaming: audio is buffered, sent once, one result returned.
Max audio payload: 25 MB (Groq hard limit).
"""
from __future__ import annotations

import io
import os
import time
from typing import AsyncIterator

from groq import AsyncGroq

from providers.base import BaseSTT, STTResult

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB — Groq hard limit


class GroqWhisperSTTAdapter(BaseSTT):
    """Buffers the audio stream and submits it to Groq Whisper-large-v3."""

    def __init__(
        self,
        language_code: str,
        confidence_threshold: float,
    ) -> None:
        super().__init__(language=language_code, confidence_threshold=confidence_threshold)
        api_key = os.environ.get("GROQ_API_KEY", "")
        if len(api_key) == 0:
            raise EnvironmentError("GROQ_API_KEY is not set")
        self._client = AsyncGroq(api_key=api_key)
        self._language_code = language_code

    @property
    def provider_name(self) -> str:
        return "groq_whisper"

    async def set_language(self, language_code: str) -> None:
        """Update language code; takes effect on the next transcribe_stream call."""
        self._language_code = language_code
        self.language = language_code

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[STTResult]:
        """Buffer audio up to MAX_AUDIO_BYTES, then transcribe; yield one final result."""
        start_time = time.monotonic()
        buffer = io.BytesIO()
        total_bytes = 0

        async for chunk in audio_stream:
            remaining = MAX_AUDIO_BYTES - total_bytes
            if len(chunk) > remaining:
                buffer.write(chunk[:remaining])
                total_bytes += remaining
                break
            buffer.write(chunk)
            total_bytes += len(chunk)

        audio_bytes = buffer.getvalue()
        if not audio_bytes:
            return

        transcription = await self._client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=("audio.wav", audio_bytes, "audio/wav"),
            language=self._language_code,
            response_format="verbose_json",
        )

        text = transcription.text.strip() if transcription.text else ""
        # Groq verbose_json does not expose a top-level confidence score;
        # use a sentinel of 1.0 — downstream thresholding operates on actual
        # ASR output quality rather than a probabilistic score.
        confidence = 1.0
        latency = int((time.monotonic() - start_time) * 1000)

        yield STTResult(
            text=text,
            confidence=confidence,
            language=self._language_code,
            is_final=True,
            provider=self.provider_name,
            latency_ms=latency,
        )
