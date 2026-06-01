from __future__ import annotations

import os
import time
from typing import AsyncIterator

from cartesia import Cartesia

from providers.base import BaseTTS, TTSResult

_MODEL_ID = "sonic-english"
_OUTPUT_FORMAT = {
    "container": "raw",
    "encoding": "pcm_f32le",
    "sample_rate": 44100,
}


class CartesiaTTSAdapter(BaseTTS):
    """Cartesia TTS adapter — English only.

    Uses sonic-english with raw PCM f32le output at 44100 Hz.
    """

    def __init__(self, language: str = "en", voice: str = "") -> None:
        if not language.startswith("en"):
            raise ValueError(
                f"CartesiaTTSAdapter only supports English (got '{language}'). "
                "Use AzureNeuralTTSAdapter for Urdu/Punjabi."
            )
        super().__init__(language=language, voice=voice)
        api_key = os.environ["CARTESIA_API_KEY"]
        self._client = Cartesia(api_key=api_key)

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        voice_params: dict = {}
        if self.voice:
            voice_params["id"] = self.voice

        for chunk in self._client.tts.sse(
            model_id=_MODEL_ID,
            transcript=text,
            voice=voice_params,
            output_format=_OUTPUT_FORMAT,  # type: ignore[arg-type]
        ):
            audio = getattr(chunk, "audio", None)
            if audio:
                yield audio

    async def synthesize(self, text: str) -> TTSResult:
        t0 = time.monotonic()
        chunks: list[bytes] = []
        async for chunk in self.synthesize_stream(text):
            chunks.append(chunk)
        latency_ms = int((time.monotonic() - t0) * 1000)

        return TTSResult(
            audio_bytes=b"".join(chunks),
            provider=self.provider_name,
            voice=self.voice,
            language=self.language,
            latency_ms=latency_ms,
        )

    async def set_language(self, language_code: str, voice: str) -> None:
        if not language_code.startswith("en"):
            raise ValueError(
                f"CartesiaTTSAdapter only supports English (got '{language_code}')."
            )
        self.language = language_code
        if voice:
            self.voice = voice

    @property
    def provider_name(self) -> str:
        return "cartesia"
