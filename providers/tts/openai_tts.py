from __future__ import annotations

import os
import time
from typing import AsyncIterator

from openai import AsyncOpenAI

from providers.base import BaseTTS, TTSResult

_MODEL = "tts-1"
_DEFAULT_VOICE = "nova"


class OpenAITTSAdapter(BaseTTS):
    """OpenAI TTS adapter using the proven nova voice on tts-1.

    Language-agnostic: the same voice is used regardless of language.
    Primary use: English.
    """

    def __init__(self, language: str = "en", voice: str = _DEFAULT_VOICE) -> None:
        super().__init__(language=language, voice=voice)
        api_key = os.environ["OPENAI_API_KEY"]
        self._client = AsyncOpenAI(api_key=api_key)

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        async with self._client.audio.speech.with_streaming_response.create(
            model=_MODEL,
            voice=self.voice,  # type: ignore[arg-type]
            input=text,
            response_format="opus",
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk

    async def synthesize(self, text: str) -> TTSResult:
        t0 = time.monotonic()
        response = await self._client.audio.speech.create(
            model=_MODEL,
            voice=self.voice,  # type: ignore[arg-type]
            input=text,
            response_format="opus",
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        audio_bytes = response.content

        return TTSResult(
            audio_bytes=audio_bytes,
            provider=self.provider_name,
            voice=self.voice,
            language=self.language,
            latency_ms=latency_ms,
        )

    async def set_language(self, language_code: str, voice: str) -> None:
        # OpenAI TTS is language-agnostic; voice is preserved unless explicitly changed.
        self.language = language_code
        if voice:
            self.voice = voice

    @property
    def provider_name(self) -> str:
        return "openai"
