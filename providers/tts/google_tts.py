from __future__ import annotations

import os
import time
from typing import AsyncIterator

from google.cloud import texttospeech_v1 as tts
from google.oauth2 import service_account

from providers.base import BaseTTS, TTSResult


class GoogleTTSAdapter(BaseTTS):
    """Google Cloud Text-to-Speech adapter.

    Streaming is simulated: Google TTS does not support native chunked streaming,
    so synthesize_stream yields the full audio in one chunk after synthesis completes.
    """

    def __init__(self, language: str = "en-US", voice: str = "en-US-Neural2-F") -> None:
        super().__init__(language=language, voice=voice)
        credentials_path = os.environ["GOOGLE_CREDENTIALS_JSON"]
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        self._client = tts.TextToSpeechAsyncClient(credentials=credentials)

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        # Google TTS has no native streaming; yield full audio as a single chunk.
        result = await self.synthesize(text)
        yield result.audio_bytes

    async def synthesize(self, text: str) -> TTSResult:
        synthesis_input = tts.SynthesisInput(text=text)
        voice_params = tts.VoiceSelectionParams(
            language_code=self.language,
            name=self.voice,
        )
        audio_config = tts.AudioConfig(
            audio_encoding=tts.AudioEncoding.LINEAR16
        )

        t0 = time.monotonic()
        response = await self._client.synthesize_speech(
            input=synthesis_input,
            voice=voice_params,
            audio_config=audio_config,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        return TTSResult(
            audio_bytes=response.audio_content,
            provider=self.provider_name,
            voice=self.voice,
            language=self.language,
            latency_ms=latency_ms,
        )

    async def set_language(self, language_code: str, voice: str) -> None:
        self.language = language_code
        self.voice = voice

    @property
    def provider_name(self) -> str:
        return "google"
