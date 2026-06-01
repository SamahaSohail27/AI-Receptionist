"""
Browser test-session WebRTC endpoint.

This is the browser test session's audio path. It mirrors the proven approach
used by the AI-Clinical-Triage system: the bot's voice and the caller's mic
ride a real **WebRTC media track** (``SmallWebRTCTransport``), so the browser
plays bot audio through a native ``<audio>`` element with the browser's own
jitter buffer + Opus decode + resampling. No hand-rolled PCM scheduling in JS
(the old WebSocket/``BrowserPCMSerializer`` path is what made bot audio
inaudible on this project).

STT / LLM transcript events ride the **WebRTC data channel** as JSON. They are
the very same ``TransportMessageUrgentFrame`` envelopes the pipeline already
emits for the WebSocket path (``{"type":"stt.partial"|"stt.final"|"llm.text"|
"llm.end"|"bot.text", ...}``) — ``SmallWebRTCConnection.send_app_message``
serialises each dict straight onto the data channel, so the browser receives
them verbatim and the existing transcript JS handles them unchanged.

Same ``ReceptionistPipeline`` as telephony and the WS path — only the transport
differs (``SmallWebRTCTransport`` instead of ``FastAPIWebsocketTransport``), so
STT / LLM / TTS / caching / language switching are all identical.

Signalling (browser → server):
    POST /test-session/offer
        body: {sdp, type, session_id, language,
               pc_id?, restart_pc?, stt_provider?, llm_provider?, tts_provider?}
        returns: {sdp, type, pc_id}

Auth: standard staff JWT — the Authorization header added by the test page's
``$store.app.api`` helper (same as ``/test-session/start``).
"""
from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from core.auth import require_any_staff
from models.auth import User
from providers.telephony.session_manager import session_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/test-session", tags=["test-session-webrtc"])


# Sample rates — identical to the WS path:
#   Input (mic → VAD):  16 kHz — Silero VAD only accepts 8/16 kHz.
#   Output (TTS → spk): 24 kHz — OpenAI TTS native; the browser/WebRTC stack
#                       resamples to the output device rate on playback.
_BROWSER_INPUT_SAMPLE_RATE: int = 16000
_BROWSER_OUTPUT_SAMPLE_RATE: int = 24000

# Live WebRTC connections keyed by pc_id, so an ICE-restart renegotiation
# reuses the existing connection (and its running pipeline) instead of
# spinning up a duplicate session.
_connections: Dict[str, Any] = {}


class OfferRequest(BaseModel):
    sdp: str
    type: str
    session_id: str
    pc_id: Optional[str] = None
    restart_pc: bool = False
    language: str = "ur-PK"
    stt_provider: Optional[str] = None
    llm_provider: Optional[str] = None
    tts_provider: Optional[str] = None


async def _run_pipeline(connection: Any, req: OfferRequest) -> None:
    """Build a SmallWebRTCTransport around the connection and run the unified
    ReceptionistPipeline until the peer disconnects. One task per session."""
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
    from pipecat.transports.base_transport import TransportParams
    from pipeline.voice_pipeline import ReceptionistPipeline, build_silero_vad

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=_BROWSER_INPUT_SAMPLE_RATE,    # 16 kHz — Silero VAD
            audio_out_sample_rate=_BROWSER_OUTPUT_SAMPLE_RATE,  # 24 kHz — OpenAI TTS native
            vad_analyzer=build_silero_vad(),
        ),
    )

    pipeline = ReceptionistPipeline(
        session_id=req.session_id,
        transport=transport,
        language=req.language,
        stt_provider=req.stt_provider,
        llm_provider=req.llm_provider,
        tts_provider=req.tts_provider,
        preselect_language=True,  # browser picks language via dropdown — no IVR wait
    )

    # Track alongside telephony sessions so the admin dashboard and analytics
    # filtering treat it uniformly (browser sessions have no caller number).
    await session_manager.register_session(req.session_id, {
        "session_id": req.session_id,
        "provider": "browser",
        "caller_number": "",
        "called_number": "",
        "state": "active",
        "test": True,
        "pipeline": pipeline,
    })

    logger.info(
        "Browser WebRTC session start: session=%s language=%s stt=%s llm=%s tts=%s",
        req.session_id, req.language, req.stt_provider or "(profile)",
        req.llm_provider or "openai", req.tts_provider or "(admin)",
    )

    try:
        await pipeline.start()  # blocks in PipelineRunner until the peer leaves
    except Exception as exc:
        logger.error("Browser WebRTC pipeline error session=%s: %s", req.session_id, exc)
    finally:
        await pipeline.stop()
        await session_manager.end_session(req.session_id)


@router.post("/offer")
async def webrtc_offer(
    req: OfferRequest,
    background_tasks: BackgroundTasks,
    user: Annotated[User, Depends(require_any_staff)],
) -> dict:
    """Handle a WebRTC SDP offer — create or reuse a connection and start the
    pipeline. Returns the SDP answer for the browser to apply."""
    # Deferred import so FastAPI startup doesn't pull aiortc/opencv into the
    # router import graph until an actual offer arrives.
    try:
        from pipecat.transports.smallwebrtc.connection import (
            IceServer,
            SmallWebRTCConnection,
        )
    except Exception as exc:  # missing aiortc / opencv → clear error to the UI
        logger.exception("WebRTC dependencies unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"WebRTC transport unavailable on the server: {exc}",
        )

    ice_servers = [
        IceServer(urls="stun:stun.l.google.com:19302"),
        IceServer(urls="stun:stun1.l.google.com:19302"),
    ]

    if req.pc_id and req.pc_id in _connections:
        connection = _connections[req.pc_id]
        logger.info("WebRTC: reusing connection %s (session=%s)", req.pc_id, req.session_id)
        await connection.renegotiate(
            sdp=req.sdp, type=req.type, restart_pc=req.restart_pc,
        )
    else:
        connection = SmallWebRTCConnection(ice_servers=ice_servers)
        await connection.initialize(sdp=req.sdp, type=req.type)

        @connection.event_handler("closed")
        async def _on_closed(conn: Any) -> None:  # noqa: ANN001
            _connections.pop(conn.pc_id, None)
            logger.info("WebRTC: connection closed %s (session=%s)", conn.pc_id, req.session_id)

        # Start the pipeline AFTER the answer is returned (exactly like the
        # triage /api/offer flow) so the browser applies the SDP answer before
        # the bot transport begins driving media.
        background_tasks.add_task(_run_pipeline, connection, req)

    answer = connection.get_answer()
    _connections[answer["pc_id"]] = connection
    logger.info("WebRTC: answer generated pc_id=%s", answer.get("pc_id"))
    return answer
