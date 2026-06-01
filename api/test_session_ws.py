"""
Browser test-session WebSocket endpoint.

This is the *only* path the browser test session takes for live voice. It
builds the exact same ``ReceptionistPipeline`` that telephony uses —
identical STT, LLM, TTS, caching, broadcasters, language switching — and
plugs it into a pipecat ``FastAPIWebsocketTransport`` configured with a
``BrowserPCMSerializer``.

Wire protocol (handled by the serializer, see pipeline/browser_serializer.py):
    Browser → server:  {"type": "audio", "data": "<base64 PCM16>"}
                       {"type": "dtmf",  "digit": "1" | "2" | "3"}
                       (closing the WS ends the session — no explicit hangup)
    Server → browser:  {"type": "audio",       "data": "<base64 PCM16>", "sample_rate": N}
                       {"type": "stt.partial", "text": "...", "language": "..."}
                       {"type": "stt.final",   "text": "...", "language": "..."}
                       {"type": "llm.text",    "text": "..."}
                       {"type": "llm.end"}

Auth: JWT supplied as ``?token=...`` query string (same as the previous
realtime endpoint — WebSockets cannot send custom headers from a browser).
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Query, WebSocket, status

from core.auth import decode_token
from core.config import settings
from providers.telephony.session_manager import session_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/test-session", tags=["test-session-ws"])


# Sample rates:
#   Input (mic → VAD):  16 kHz — Silero VAD only accepts 8 kHz or 16 kHz,
#                       so the browser mic is captured at 16 kHz.
#   Output (TTS → spk): 24 kHz — matches OpenAI TTS native rate so no
#                       server-side resampling is needed; the browser
#                       AudioContext resamples 24 kHz buffers on playback.
_BROWSER_INPUT_SAMPLE_RATE: int = 16000
_BROWSER_OUTPUT_SAMPLE_RATE: int = 24000


@router.websocket("/ws/{session_id}")
async def test_session_ws(
    ws: WebSocket,
    session_id: str,
    token: str = Query(..., description="JWT issued by /auth/token"),
    language: Literal["ur-PK", "pa-PK", "en"] = Query(
        "ur-PK", description="Initial language; DTMF buttons can switch at runtime."
    ),
    stt_provider: Optional[str] = Query(
        None, description="STT override: deepgram | openai_whisper | groq_whisper. "
                          "Omitted → language-profile default."
    ),
    llm_provider: Optional[str] = Query(
        None, description="LLM override: openai | groq | anthropic. Omitted → openai."
    ),
    tts_provider: Optional[str] = Query(
        None, description="TTS override: openai | elevenlabs | azure. "
                          "Omitted → admin runtime_config / profile default."
    ),
) -> None:
    """Single browser voice path — same pipeline as telephony, browser serializer."""
    # Auth — close before accept on failure so the browser sees a clean 1008.
    try:
        decode_token(token)
    except Exception:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Import deferred so the module loads cheaply (avoids pulling pipecat
    # into the FastAPI router import graph until an actual WS connect).
    try:
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketTransport,
            FastAPIWebsocketParams,
        )
        from pipeline.browser_serializer import BrowserPCMSerializer
        from pipeline.voice_pipeline import ReceptionistPipeline, build_silero_vad
    except Exception as exc:
        # Surface the failure so the browser sees a clean error instead of an
        # opaque immediate-close — and the operator sees the traceback.
        logger.exception("test_session_ws: pipeline import failed: %s", exc)
        await ws.accept()
        try:
            await ws.send_text(
                '{"type":"error","error":{"message":"server pipeline import failed: '
                + str(exc).replace('"', "'")
                + '"}}'
            )
        finally:
            await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    await ws.accept()

    serializer = BrowserPCMSerializer(sample_rate=_BROWSER_INPUT_SAMPLE_RATE)
    params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=_BROWSER_INPUT_SAMPLE_RATE,    # 16 kHz — Silero VAD
        audio_out_sample_rate=_BROWSER_OUTPUT_SAMPLE_RATE,  # 24 kHz — OpenAI TTS native
        add_wav_header=False,
        vad_analyzer=build_silero_vad(),
        serializer=serializer,
    )
    transport = FastAPIWebsocketTransport(websocket=ws, params=params)

    pipeline = ReceptionistPipeline(
        session_id=session_id,
        transport=transport,
        language=language,
        stt_provider=stt_provider,
        llm_provider=llm_provider,
        tts_provider=tts_provider,
        preselect_language=True,  # browser picks language via dropdown — no IVR wait
    )

    # Track this session alongside telephony sessions so the admin dashboard
    # and DTMF webhook patterns work uniformly. The browser doesn't have a
    # caller number, so we mark it as a test session for analytics filtering.
    await session_manager.register_session(session_id, {
        "session_id": session_id,
        "provider": "browser",
        "caller_number": "",
        "called_number": "",
        "state": "active",
        "test": True,
        "pipeline": pipeline,
    })

    logger.info(
        "Browser test-session WS connected: session=%s language=%s "
        "stt=%s llm=%s tts=%s",
        session_id, language, stt_provider or "(profile)",
        llm_provider or "openai", tts_provider or "(admin)",
    )

    try:
        await pipeline.start()
    except Exception as exc:
        logger.error("Browser pipeline error session=%s: %s", session_id, exc)
    finally:
        await pipeline.stop()
        await session_manager.end_session(session_id)
