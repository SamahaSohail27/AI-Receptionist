"""
Deepgram Nova-2 STT adapter — WebSocket streaming via deepgram-sdk.

Endpointing: 600 ms, utterance_end_ms: 1500 ms, no_delay: True, vad_events: True.
Confidence thresholds (SACRED): Urdu=0.45, English=0.70, Punjabi=0.40.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents,
)

from providers.base import BaseSTT, STTResult


class DeepgramSTTAdapter(BaseSTT):
    """Streams audio to Deepgram Nova-2 over a persistent WebSocket connection."""

    def __init__(
        self,
        language_code: str,
        confidence_threshold: float,
        api_key: str | None = None,
    ) -> None:
        super().__init__(language=language_code, confidence_threshold=confidence_threshold)
        resolved_key = api_key or os.environ.get("DEEPGRAM_API_KEY", "")
        if len(resolved_key) == 0:
            raise EnvironmentError("DEEPGRAM_API_KEY is not set")
        self._client = DeepgramClient(
            resolved_key,
            DeepgramClientOptions(options={"keepalive": "true"}),
        )
        self._language_code = language_code

    @property
    def provider_name(self) -> str:
        return "deepgram"

    async def set_language(self, language_code: str) -> None:
        """Store language code; takes effect on the next transcribe_stream call."""
        self._language_code = language_code
        self.language = language_code

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[STTResult]:
        """Open a Deepgram LiveTranscription WebSocket, feed audio, yield STTResults."""
        result_queue: asyncio.Queue[STTResult | None] = asyncio.Queue()
        start_time = time.monotonic()

        connection = self._client.listen.asynclive.v("1")

        async def on_message(_self, result, **kwargs) -> None:  # type: ignore[no-untyped-def]
            sentence = result.channel.alternatives[0]
            if not sentence.transcript:
                return
            confidence = float(sentence.confidence) if sentence.confidence is not None else 0.0
            latency = int((time.monotonic() - start_time) * 1000)
            await result_queue.put(
                STTResult(
                    text=sentence.transcript,
                    confidence=confidence,
                    language=self._language_code,
                    is_final=result.is_final,
                    provider=self.provider_name,
                    latency_ms=latency,
                )
            )

        async def on_utterance_end(_self, utterance_end, **kwargs) -> None:  # type: ignore[no-untyped-def]
            # Signal that an utterance has ended; downstream may use this to flush.
            pass

        async def on_error(_self, error, **kwargs) -> None:  # type: ignore[no-untyped-def]
            await result_queue.put(None)

        async def on_close(_self, close, **kwargs) -> None:  # type: ignore[no-untyped-def]
            await result_queue.put(None)

        connection.on(LiveTranscriptionEvents.Transcript, on_message)
        connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
        connection.on(LiveTranscriptionEvents.Error, on_error)
        connection.on(LiveTranscriptionEvents.Close, on_close)

        options = LiveOptions(
            language=self._language_code,
            model="nova-2",
            endpointing=600,
            utterance_end_ms="1500",
            no_delay=True,
            vad_events=True,
            smart_format=True,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
        )

        await connection.start(options)

        async def feed_audio() -> None:
            try:
                async for chunk in audio_stream:
                    await connection.send(chunk)
            finally:
                await connection.finish()

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
