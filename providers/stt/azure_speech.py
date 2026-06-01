"""
Azure Cognitive Services Speech STT adapter — streaming via PushAudioInputStream.

Uses the azure-cognitiveservices-speech SDK. Audio chunks are pushed into a
PushAudioInputStream; recognized events are forwarded as STTResults.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator

import azure.cognitiveservices.speech as speechsdk

from providers.base import BaseSTT, STTResult


class AzureSpeechSTTAdapter(BaseSTT):
    """Feeds a live audio stream to Azure Speech via PushAudioInputStream."""

    def __init__(
        self,
        language_code: str,
        confidence_threshold: float,
    ) -> None:
        super().__init__(language=language_code, confidence_threshold=confidence_threshold)
        self._speech_key = os.environ.get("AZURE_SPEECH_KEY", "")
        self._speech_region = os.environ.get("AZURE_SPEECH_REGION", "")
        if len(self._speech_key) == 0:
            raise EnvironmentError("AZURE_SPEECH_KEY is not set")
        if len(self._speech_region) == 0:
            raise EnvironmentError("AZURE_SPEECH_REGION is not set")
        self._language_code = language_code

    @property
    def provider_name(self) -> str:
        return "azure_speech"

    async def set_language(self, language_code: str) -> None:
        """Store language code; takes effect on the next transcribe_stream call."""
        self._language_code = language_code
        self.language = language_code

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[STTResult]:
        """Push audio chunks to Azure Speech and yield STTResults from recognized events."""
        loop = asyncio.get_running_loop()
        result_queue: asyncio.Queue[STTResult | None] = asyncio.Queue()
        start_time = time.monotonic()

        speech_config = speechsdk.SpeechConfig(
            subscription=self._speech_key,
            region=self._speech_region,
        )
        speech_config.speech_recognition_language = self._language_code
        # Output detailed JSON so we can read confidence from the result.
        speech_config.output_format = speechsdk.OutputFormat.Detailed

        push_stream = speechsdk.audio.PushAudioInputStream()
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

        recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        def _on_recognizing(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
            if not evt.result.text:
                return
            latency = int((time.monotonic() - start_time) * 1000)
            loop.call_soon_threadsafe(
                result_queue.put_nowait,
                STTResult(
                    text=evt.result.text,
                    confidence=0.0,  # partial results do not carry confidence
                    language=self._language_code,
                    is_final=False,
                    provider=self.provider_name,
                    latency_ms=latency,
                ),
            )

        def _on_recognized(evt: speechsdk.SpeechRecognitionEventArgs) -> None:
            if evt.result.reason != speechsdk.ResultReason.RecognizedSpeech:
                return
            text = evt.result.text
            if not text:
                return
            # Extract best confidence from detailed JSON when available.
            confidence = 0.0
            try:
                import json
                detail = json.loads(evt.result.json)
                nbest = detail.get("NBest", [])
                if nbest:
                    confidence = float(nbest[0].get("Confidence", 0.0))
            except Exception:
                pass
            latency = int((time.monotonic() - start_time) * 1000)
            loop.call_soon_threadsafe(
                result_queue.put_nowait,
                STTResult(
                    text=text,
                    confidence=confidence,
                    language=self._language_code,
                    is_final=True,
                    provider=self.provider_name,
                    latency_ms=latency,
                ),
            )

        def _on_canceled(evt: speechsdk.SpeechRecognitionCanceledEventArgs) -> None:
            loop.call_soon_threadsafe(result_queue.put_nowait, None)

        def _on_session_stopped(evt: speechsdk.SessionEventArgs) -> None:
            loop.call_soon_threadsafe(result_queue.put_nowait, None)

        recognizer.recognizing.connect(_on_recognizing)
        recognizer.recognized.connect(_on_recognized)
        recognizer.canceled.connect(_on_canceled)
        recognizer.session_stopped.connect(_on_session_stopped)

        recognizer.start_continuous_recognition_async()

        async def feed_audio() -> None:
            try:
                async for chunk in audio_stream:
                    push_stream.write(chunk)
            finally:
                push_stream.close()

        feeder = asyncio.create_task(feed_audio())

        try:
            while True:
                item = await result_queue.get()
                if item is None:
                    break
                yield item
        finally:
            recognizer.stop_continuous_recognition_async()
            feeder.cancel()
            try:
                await feeder
            except asyncio.CancelledError:
                pass
