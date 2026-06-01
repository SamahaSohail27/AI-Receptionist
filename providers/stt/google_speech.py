"""
Google Cloud Speech-to-Text STT adapter — streaming via streaming_recognize().

Uses google-cloud-speech async client. Credentials are loaded from a service
account JSON file whose path is given by GOOGLE_CREDENTIALS_JSON.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator

from google.cloud import speech
from google.oauth2 import service_account

from providers.base import BaseSTT, STTResult


class GoogleSTTAdapter(BaseSTT):
    """Streams audio to Google Cloud Speech via streaming_recognize."""

    def __init__(
        self,
        language_code: str,
        confidence_threshold: float,
    ) -> None:
        super().__init__(language=language_code, confidence_threshold=confidence_threshold)
        credentials_path = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
        if len(credentials_path) == 0:
            raise EnvironmentError("GOOGLE_CREDENTIALS_JSON is not set")
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        self._client = speech.SpeechAsyncClient(credentials=credentials)
        self._language_code = language_code

    @property
    def provider_name(self) -> str:
        return "google_speech"

    async def set_language(self, language_code: str) -> None:
        """Update language code; takes effect on the next transcribe_stream call."""
        self._language_code = language_code
        self.language = language_code

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[STTResult]:
        """Feed audio to Google streaming_recognize; yield one STTResult per response."""
        start_time = time.monotonic()

        recognition_config = speech.RecognitionConfig(
            language_code=self._language_code,
            model="latest_long",
            enable_automatic_punctuation=True,
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=True,
        )

        async def _request_generator() -> AsyncIterator[speech.StreamingRecognizeRequest]:
            # First request carries the config only.
            yield speech.StreamingRecognizeRequest(
                streaming_config=streaming_config
            )
            async for chunk in audio_stream:
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        responses = await self._client.streaming_recognize(
            requests=_request_generator()
        )

        async for response in responses:
            for result in response.results:
                if not result.alternatives:
                    continue
                best = result.alternatives[0]
                latency = int((time.monotonic() - start_time) * 1000)
                yield STTResult(
                    text=best.transcript.strip(),
                    confidence=float(best.confidence),
                    language=self._language_code,
                    is_final=result.is_final,
                    provider=self.provider_name,
                    latency_ms=latency,
                )
