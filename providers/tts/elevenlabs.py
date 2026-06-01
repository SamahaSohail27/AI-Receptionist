from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator

from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

from providers.base import BaseTTS, TTSResult

# CRITICAL: eleven_flash_v2_5 default. Allowed companions are multilingual_v2 and
# turbo_v2_5. Any v3 model is blocked at the adapter (HTTP 403 from upstream).
_DEFAULT_MODEL = "eleven_flash_v2_5"
ALLOWED_MODELS: tuple[str, ...] = (
    "eleven_flash_v2_5",
    "eleven_multilingual_v2",
    "eleven_turbo_v2_5",
)


def _validate_model(model_id: str) -> str:
    """Coerce + validate. v3 → ValueError; unknown → flash."""
    if not model_id:
        return _DEFAULT_MODEL
    if model_id.startswith("eleven_v3"):
        raise ValueError(
            f"ElevenLabs model {model_id!r} is blocked: upstream returns HTTP 403. "
            "Use eleven_flash_v2_5, eleven_multilingual_v2, or eleven_turbo_v2_5."
        )
    if model_id not in ALLOWED_MODELS:
        return _DEFAULT_MODEL
    return model_id

# Public ElevenLabs preset voice ids (Voice Library defaults).
# All multilingual-capable so they handle ur-PK / pa-PK / en input from the LLM.
# `description` is shown in the settings UI dropdown.
VOICE_PRESETS: list[dict] = [
    # ----- Female -----
    {
        "voice_id": "EXAVITQu4vr4xnSDxMaL",
        "name": "Bella",
        "gender": "female",
        "description": "Warm, soft, conversational — clinic receptionist feel",
    },
    {
        "voice_id": "pFZP5JQG7iQjIQuC4Bku",
        "name": "Lily",
        "gender": "female",
        "description": "Calm British, friendly and natural",
    },
    {
        "voice_id": "XB0fDUnXU5powFXDhCwa",
        "name": "Charlotte",
        "gender": "female",
        "description": "Mature, gentle, reassuring",
    },
    {
        "voice_id": "21m00Tcm4TlvDq8ikWAM",
        "name": "Rachel",
        "gender": "female",
        "description": "Clear American, neutral narration",
    },
    {
        "voice_id": "AZnzlk1XvdvUeBnXmlld",
        "name": "Domi",
        "gender": "female",
        "description": "Confident, energetic younger voice",
    },
    # ----- Male -----
    {
        "voice_id": "IKne3meq5aSn9XLyUdCD",
        "name": "Charlie",
        "gender": "male",
        "description": "Natural, conversational Australian",
    },
    {
        "voice_id": "JBFqnCBsd6RMkjVDRZzb",
        "name": "George",
        "gender": "male",
        "description": "Warm British, calm and trustworthy",
    },
    {
        "voice_id": "onwK4e9ZLuTAKqWW03F9",
        "name": "Daniel",
        "gender": "male",
        "description": "Measured British, professional",
    },
    {
        "voice_id": "pNInz6obpgDQGcFmaJgB",
        "name": "Adam",
        "gender": "male",
        "description": "Deep American, narrative tone",
    },
    {
        "voice_id": "ErXwobaYiN019PkySvjV",
        "name": "Antoni",
        "gender": "male",
        "description": "Friendly American, well-rounded",
    },
]

# First female / first male serve as fallbacks if no admin selection.
DEFAULT_FEMALE_VOICE_ID = next(v["voice_id"] for v in VOICE_PRESETS if v["gender"] == "female")
DEFAULT_MALE_VOICE_ID = next(v["voice_id"] for v in VOICE_PRESETS if v["gender"] == "male")
_DEFAULT_VOICE_ID = DEFAULT_FEMALE_VOICE_ID

MODEL_PRESETS: list[dict] = [
    {"model_id": "eleven_flash_v2_5", "name": "Flash v2.5", "description": "Fastest — lowest latency, recommended for live calls"},
    {"model_id": "eleven_multilingual_v2", "name": "Multilingual v2", "description": "Most natural prosody, slightly slower"},
    {"model_id": "eleven_turbo_v2_5", "name": "Turbo v2.5", "description": "Balanced quality and latency"},
]

# Tuned for natural, conversational delivery (less robotic than SDK defaults).
# stability lower → more expressive prosody; style mild → warmth without overacting.
_NATURAL_VOICE_SETTINGS = VoiceSettings(
    stability=0.4,
    similarity_boost=0.75,
    style=0.3,
    use_speaker_boost=True,
)


class ElevenLabsTTSAdapter(BaseTTS):
    """ElevenLabs TTS adapter (v2 SDK).

    Uses eleven_flash_v2_5 exclusively (v3 is forbidden — HTTP 403).
    VoiceSettings tuned for natural, conversational delivery.
    """

    def __init__(
        self,
        language: str = "en",
        voice: str = _DEFAULT_VOICE_ID,
        model_id: str = _DEFAULT_MODEL,
        output_format: str = "mp3_44100_128",
    ) -> None:
        super().__init__(language=language, voice=voice)
        self._client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
        self._model_id = _validate_model(model_id)
        self._output_format = output_format

    async def synthesize_stream(
        self, text: str, output_format: str | None = None
    ) -> AsyncIterator[bytes]:
        """Stream raw audio bytes for `text`. Caller picks the format.

        For browser realtime (24 kHz PCM16, signed little-endian) pass
        ``output_format='pcm_24000'``; for phone (8 kHz mu-law) pass
        ``'ulaw_8000'``; default is the constructor value (MP3).

        True streaming — chunks reach the caller as ElevenLabs emits them. The
        upstream SDK is sync; we drive it from a worker thread and forward each
        chunk through an asyncio.Queue.
        """
        loop = asyncio.get_running_loop()
        fmt = output_format or self._output_format
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        sentinel = object()

        def _producer() -> None:
            try:
                stream = self._client.text_to_speech.stream(
                    voice_id=self.voice,
                    text=text,
                    model_id=self._model_id,
                    voice_settings=_NATURAL_VOICE_SETTINGS,
                    output_format=fmt,
                )
                for chunk in stream:
                    if chunk:
                        asyncio.run_coroutine_threadsafe(queue.put(chunk), loop).result()
            except Exception as exc:  # noqa: BLE001
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(sentinel), loop).result()

        loop.run_in_executor(None, _producer)
        while True:
            item = await queue.get()
            if item is sentinel:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    async def synthesize(self, text: str) -> TTSResult:
        t0 = time.monotonic()
        loop = asyncio.get_running_loop()

        def _convert():
            return self._client.text_to_speech.convert(
                voice_id=self.voice,
                text=text,
                model_id=self._model_id,
                voice_settings=_NATURAL_VOICE_SETTINGS,
            )

        raw = await loop.run_in_executor(None, _convert)
        # `convert` returns an iterator of bytes chunks in v2 SDK.
        if isinstance(raw, (bytes, bytearray)):
            audio_bytes = bytes(raw)
        else:
            audio_bytes = b"".join(raw)
        latency_ms = int((time.monotonic() - t0) * 1000)

        return TTSResult(
            audio_bytes=audio_bytes,
            provider=self.provider_name,
            voice=self.voice,
            language=self.language,
            latency_ms=latency_ms,
        )

    async def set_language(self, language_code: str, voice: str) -> None:
        # ElevenLabs is multilingual — language is tracked for context only.
        self.language = language_code
        if voice:
            self.voice = voice

    @property
    def provider_name(self) -> str:
        return "elevenlabs"
