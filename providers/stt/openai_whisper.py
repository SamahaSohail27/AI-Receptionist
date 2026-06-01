"""
OpenAI Whisper-1 STT adapter — batch transcription via openai async client.

Same buffer-and-submit pattern as GroqWhisperSTTAdapter.
Used as a secondary fallback in the STT circuit breaker chain.
"""
from __future__ import annotations

import io
import os
import time
from typing import AsyncIterator

from openai import AsyncOpenAI

from providers.base import BaseSTT, STTResult

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB — matches OpenAI file upload limit


class OpenAIWhisperSTTAdapter(BaseSTT):
    """Buffers the audio stream and submits it to OpenAI Whisper-1."""

    def __init__(
        self,
        language_code: str,
        confidence_threshold: float,
    ) -> None:
        super().__init__(language=language_code, confidence_threshold=confidence_threshold)
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if len(api_key) == 0:
            raise EnvironmentError("OPENAI_API_KEY is not set")
        self._client = AsyncOpenAI(api_key=api_key)
        self._language_code = language_code

    @property
    def provider_name(self) -> str:
        return "openai_whisper"

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
            model="whisper-1",
            file=("audio.wav", audio_bytes, "audio/wav"),
            language=self._language_code,
            response_format="verbose_json",
        )

        text = transcription.text.strip() if transcription.text else ""
        # OpenAI verbose_json does not expose a per-utterance confidence score.
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
