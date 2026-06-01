"""
Telephony provider webhook handlers.
Plivo/Twilio/Telnyx call these when an inbound call arrives or DTMF is pressed.

The audio WebSocket endpoints build a pipecat ``FastAPIWebsocketTransport``
with the provider-specific frame serializer (Twilio mulaw or Plivo mulaw)
and hand it to the same ``ReceptionistPipeline`` that powers the browser
test session — the pipeline body is identical for both; only the transport
+ serializer differ.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Request, Response, WebSocket
from fastapi.responses import PlainTextResponse

from core.config import settings
from providers.telephony.session_manager import session_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telephony", tags=["telephony"])


def _get_pipeline_for_session(session_id: str):
    """Get active ReceptionistPipeline for a session (set by pipeline startup)."""
    sess = session_manager.get_all_active()
    for s in sess:
        if s.get("session_id") == session_id:
            return s.get("pipeline")
    return None


# ---------------------------------------------------------------------------
# Plivo webhooks
# ---------------------------------------------------------------------------

@router.post("/plivo/inbound")
async def plivo_inbound(request: Request, background: BackgroundTasks) -> Response:
    """Plivo calls this when a new inbound call arrives."""
    form = await request.form()
    data = dict(form)
    session_id = data.get("CallUUID", "")
    caller = data.get("From", "")
    called = data.get("To", "")

    logger.info("Plivo inbound: session=%s from=%s", session_id, caller[-4:] if caller else "?")

    await session_manager.register_session(session_id, {
        "session_id": session_id,
        "provider": "plivo",
        "caller_number": caller,
        "called_number": called,
        "state": "ringing",
    })

    # Respond with Plivo XML to connect call to our WebSocket pipeline
    ws_url = f"wss://{request.headers.get('host', 'localhost')}/telephony/plivo/ws/{session_id}"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Stream streamTimeout="86400" keepCallAlive="true" bidirectional="true" contentType="audio/x-mulaw;rate=8000" audioTrack="inbound">
    {ws_url}
  </Stream>
</Response>"""
    return Response(content=xml, media_type="application/xml")


@router.post("/plivo/dtmf")
async def plivo_dtmf(request: Request) -> Response:
    form = await request.form()
    data = dict(form)
    session_id = data.get("CallUUID", "")
    digit = data.get("Digit", "")

    if digit in ("1", "2", "3"):
        pipeline = _get_pipeline_for_session(session_id)
        if pipeline and hasattr(pipeline, "_dtmf_selector"):
            pipeline._dtmf_selector.handle_dtmf(digit)

    return Response(content="", status_code=204)


@router.post("/plivo/status")
async def plivo_status(request: Request) -> Response:
    form = await request.form()
    data = dict(form)
    session_id = data.get("CallUUID", "")
    status = data.get("CallStatus", "")
    if status in ("completed", "failed", "busy", "no-answer"):
        await session_manager.end_session(session_id)
    return Response(content="", status_code=204)


# ---------------------------------------------------------------------------
# Twilio webhooks
# ---------------------------------------------------------------------------

@router.post("/twilio/inbound")
async def twilio_inbound(request: Request) -> Response:
    form = await request.form()
    data = dict(form)
    session_id = data.get("CallSid", "")
    caller = data.get("From", "")
    called = data.get("To", "")

    logger.info("Twilio inbound: session=%s from=%s", session_id, caller[-4:] if caller else "?")

    await session_manager.register_session(session_id, {
        "session_id": session_id,
        "provider": "twilio",
        "caller_number": caller,
        "called_number": called,
        "state": "ringing",
    })

    ws_url = f"wss://{request.headers.get('host', 'localhost')}/telephony/twilio/ws/{session_id}"
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}"/>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.post("/twilio/status")
async def twilio_status(request: Request) -> Response:
    form = await request.form()
    data = dict(form)
    session_id = data.get("CallSid", "")
    status = data.get("CallStatus", "")
    if status in ("completed", "failed", "busy", "no-answer"):
        await session_manager.end_session(session_id)
    return Response(content="", status_code=204)


# ---------------------------------------------------------------------------
# Pipeline WebSocket endpoints
#
# One per telephony provider so we can construct the right pipecat frame
# serializer (Twilio/Plivo speak mulaw 8 kHz inside provider-specific JSON
# envelopes — each has its own ``stream_id`` / ``streamSid`` handshake).
# Both build the *same* ``ReceptionistPipeline`` — only the transport and
# serializer differ from the browser test-session endpoint.
# ---------------------------------------------------------------------------

async def _peek_start_message(ws: WebSocket) -> dict:
    """Read text frames until we see a provider 'start' event and return it.

    Both Twilio and Plivo emit a ``{"event": "start", ...}`` message first
    that carries the stream_sid / streamId. Any earlier ``connected`` events
    are skipped silently — they carry no information we need.
    """
    while True:
        raw = await ws.receive_text()
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(msg, dict) and msg.get("event") == "start":
            return msg


async def _run_telephony_pipeline(
    ws: WebSocket,
    session_id: str,
    serializer,
) -> None:
    """Common driver: build pipecat transport, run ReceptionistPipeline."""
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketTransport,
        FastAPIWebsocketParams,
    )
    from pipeline.voice_pipeline import ReceptionistPipeline, build_silero_vad

    params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        audio_in_sample_rate=8000,        # Twilio/Plivo native rate
        audio_out_sample_rate=8000,
        add_wav_header=False,
        vad_analyzer=build_silero_vad(),  # SACRED VAD constants
        serializer=serializer,
    )
    transport = FastAPIWebsocketTransport(websocket=ws, params=params)

    pipeline = ReceptionistPipeline(
        session_id=session_id,
        transport=transport,
        language=settings.default_language,  # DTMF will override
    )
    await session_manager.update_session(
        session_id, {"pipeline": pipeline, "state": "active"}
    )

    try:
        await pipeline.start()
    except Exception as exc:
        logger.error("Telephony pipeline error session=%s: %s", session_id, exc)
    finally:
        await pipeline.stop()
        await session_manager.end_session(session_id)


@router.websocket("/plivo/ws/{session_id}")
async def plivo_pipeline_websocket(ws: WebSocket, session_id: str) -> None:
    """Plivo audio WebSocket — mulaw 8 kHz, JSON envelope with streamId."""
    from pipecat.serializers.plivo import PlivoFrameSerializer

    await ws.accept()
    start_msg = await _peek_start_message(ws)
    start = start_msg.get("start", {}) if isinstance(start_msg, dict) else {}
    stream_id = start.get("streamId", "")
    call_id = start.get("callId")

    if not stream_id:
        logger.error("Plivo WS: missing streamId in start event for session=%s", session_id)
        await ws.close()
        return

    serializer = PlivoFrameSerializer(stream_id=stream_id, call_id=call_id)
    logger.info(
        "Plivo WS connected: session=%s stream=%s call=%s",
        session_id, stream_id[:8], (call_id or "")[:8],
    )
    await _run_telephony_pipeline(ws, session_id, serializer)


@router.websocket("/twilio/ws/{session_id}")
async def twilio_pipeline_websocket(ws: WebSocket, session_id: str) -> None:
    """Twilio audio WebSocket — mulaw 8 kHz, JSON envelope with streamSid."""
    from pipecat.serializers.twilio import TwilioFrameSerializer

    await ws.accept()
    start_msg = await _peek_start_message(ws)
    start = start_msg.get("start", {}) if isinstance(start_msg, dict) else {}
    stream_sid = start.get("streamSid", "")
    call_sid = start.get("callSid")

    if not stream_sid:
        logger.error("Twilio WS: missing streamSid in start event for session=%s", session_id)
        await ws.close()
        return

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=settings.twilio_account_sid if hasattr(settings, "twilio_account_sid") else None,
        auth_token=settings.twilio_auth_token if hasattr(settings, "twilio_auth_token") else None,
    )
    logger.info(
        "Twilio WS connected: session=%s stream=%s call=%s",
        session_id, stream_sid[:8], (call_sid or "")[:8],
    )
    await _run_telephony_pipeline(ws, session_id, serializer)
