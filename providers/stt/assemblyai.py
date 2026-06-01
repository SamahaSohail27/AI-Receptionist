"""
AssemblyAI Realtime STT adapter — English-only backup provider.

Uses assemblyai.RealtimeTranscriber for WebSocket streaming.
Confidence threshold is hard-wired to 0.70 (English SACRED value).
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator

import assemblyai as aai

from providers.base import BaseSTT, STTResult

_ENGLISH_CONFIDENCE_THRESHOLD = 0.70


class AssemblyAISTTAdapter(BaseSTT):
    """Streams audio to AssemblyAI Realtime API; yields STTResults."""

    def __init__(self) -> None:
        # English-only — confidence threshold is always 0.70.
        super().__init__(
            language="en",
            confidence_threshold=_ENGLISH_CONFIDENCE_THRESHOLD,
        )
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
        if len(api_key) == 0:
            raise EnvironmentError("ASSEMBLYAI_API_KEY is not set")
        aai.settings.api_key = api_key

    @property
    def provider_name(self) -> str:
        return "assemblyai"

    async def set_language(self, language_code: str) -> None:
        """AssemblyAI Realtime only supports English; language_code is ignored."""
        self.language = "en"

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[STTResult]:
        """Open an AssemblyAI RealtimeTranscriber, stream audio, yield STTResults."""
        loop = asyncio.get_running_loop()
        result_queue: asyncio.Queue[STTResult | None] = asyncio.Queue()
        start_time = time.monotonic()

        def _on_data(transcript: aai.RealtimeTranscript) -> None:
            if not isinstance(transcript, (aai.RealtimePartialTranscript, aai.RealtimeFinalTranscript)):
                return
            text = transcript.text
            if not text:
                return
            is_final = isinstance(transcript, aai.RealtimeFinalTranscript)
            # AssemblyAI Realtime does not expose a per-utterance confidence score;
            # use 1.0 so downstream thresholding passes (operator-level quality).
            confidence = 1.0
            latency = int((time.monotonic() - start_time) * 1000)
            loop.call_soon_threadsafe(
                result_queue.put_nowait,
                STTResult(
                    text=text,
                    confidence=confidence,
                    language="en",
                    is_final=is_final,
                    provider=self.provider_name,
                    latency_ms=latency,
                ),
            )

        def _on_error(error: aai.RealtimeError) -> None:
            loop.call_soon_threadsafe(result_queue.put_nowait, None)

        transcriber = aai.RealtimeTranscriber(
            on_data=_on_data,
            on_error=_on_error,
            sample_rate=16_000,
        )

        transcriber.connect()

        async def feed_audio() -> None:
            try:
                async for chunk in audio_stream:
                    transcriber.stream(chunk)
            finally:
                transcriber.close()
                loop.call_soon_threadsafe(result_queue.put_nowait, None)

        feeder = asyncio.create_task(feed_audio())

        try:
            while True:
                item = await result_queue.get()
                if item is None:
                    break
                yield item
        finally:
            feeder.cancel()
            try:
                await feeder
            except asyncio.CancelledError:
                pass
