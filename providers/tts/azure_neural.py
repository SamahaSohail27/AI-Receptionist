from __future__ import annotations

import os
import time
from typing import AsyncIterator

import azure.cognitiveservices.speech as speechsdk

from providers.base import BaseTTS, TTSResult


class AzureNeuralTTSAdapter(BaseTTS):
    """Azure Cognitive Services Neural TTS adapter.

    Primary TTS for Urdu (ur-PK-UzmaNeural) and Punjabi (ur-PK fallback).
    """

    _DEFAULT_VOICES: dict[str, str] = {
        "ur-PK": "ur-PK-UzmaNeural",
        "pa-PK": "ur-PK-UzmaNeural",  # Punjabi falls back to Urdu neural voice
        "en-US": "en-US-JennyNeural",
        "en":    "en-US-JennyNeural",
    }

    def __init__(self, language: str = "ur-PK", voice: str | None = None) -> None:
        resolved_voice = voice or self._DEFAULT_VOICES.get(language, "ur-PK-UzmaNeural")
        super().__init__(language=language, voice=resolved_voice)

        self._key = os.environ["AZURE_SPEECH_KEY"]
        self._region = os.environ["AZURE_SPEECH_REGION"]
        self._speech_config = self._build_speech_config()

    def _build_speech_config(self) -> speechsdk.SpeechConfig:
        cfg = speechsdk.SpeechConfig(subscription=self._key, region=self._region)
        cfg.speech_synthesis_voice_name = self.voice
        cfg.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3
        )
        return cfg

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        pull_stream = speechsdk.audio.PullAudioOutputStream()
        audio_config = speechsdk.audio.AudioOutputConfig(stream=pull_stream)
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=self._speech_config, audio_config=audio_config
        )

        result = synthesizer.speak_text_async(text).get()

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            # Read all audio from the pull stream in chunks
            chunk_size = 4096
            while True:
                chunk = bytes(chunk_size)
                filled = pull_stream.read(chunk)
                if filled == 0:
                    break
                yield chunk[:filled]
        elif result.reason == speechsdk.ResultReason.Canceled:
            details = speechsdk.SpeechSynthesisCancellationDetails(result)
            raise RuntimeError(f"Azure TTS canceled: {details.error_details}")

    async def synthesize(self, text: str) -> TTSResult:
        pull_stream = speechsdk.audio.PullAudioOutputStream()
        audio_config = speechsdk.audio.AudioOutputConfig(stream=pull_stream)
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=self._speech_config, audio_config=audio_config
        )

        t0 = time.monotonic()
        result = synthesizer.speak_text_async(text).get()
        latency_ms = int((time.monotonic() - t0) * 1000)

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            audio_data = result.audio_data
        elif result.reason == speechsdk.ResultReason.Canceled:
            details = speechsdk.SpeechSynthesisCancellationDetails(result)
            raise RuntimeError(f"Azure TTS canceled: {details.error_details}")
        else:
            raise RuntimeError(f"Azure TTS unexpected result: {result.reason}")

        return TTSResult(
            audio_bytes=audio_data,
            provider=self.provider_name,
            voice=self.voice,
            language=self.language,
            latency_ms=latency_ms,
        )

    async def set_language(self, language_code: str, voice: str) -> None:
        self.language = language_code
        self.voice = voice or self._DEFAULT_VOICES.get(language_code, voice)
        self._speech_config = self._build_speech_config()

    @property
    def provider_name(self) -> str:
        return "azure"
