"""
AI Medical Receptionist — FastAPI application entry point.

Serves on http://localhost:8000
WebSocket live updates on ws://localhost:8000/ws
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.config import settings
from core.ws_manager import ws_manager

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting AI Medical Receptionist...")

    # Run Alembic migrations in dev mode
    if settings.debug:
        from alembic.config import Config
        from alembic import command
        alembic_cfg = Config("alembic.ini")
        await asyncio.get_event_loop().run_in_executor(
            None, command.upgrade, alembic_cfg, "head"
        )
        logger.info("Alembic migrations applied")

    # Pre-warm TTS cache for greetings in all 3 languages
    try:
        from pipeline.tts_cache import warm_tts_cache
        asyncio.create_task(warm_tts_cache())
    except ImportError:
        pass  # pipeline module built in Phase 8.4

    logger.info("AI Receptionist ready")
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="AI Medical Receptionist",
    description="Pakistani hospital AI receptionist — ur-PK | pa-PK | en",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# API Routers
# ---------------------------------------------------------------------------

def _include_routers() -> None:
    from api.auth import router as auth_router
    from api.patients import router as patients_router
    from api.doctors import router as doctors_router
    from api.appointments import router as appointments_router
    from api.calls import router as calls_router
    from api.analytics import router as analytics_router
    from api.providers import router as providers_router, settings_router as provider_settings_router
    from api.language import router as language_router
    from api.clinic import router as clinic_router
    from api.health import router as health_router
    from api.scheduling import router as scheduling_router
    from api.test_session import router as test_session_router
    # Unified voice path: browser test session + telephony share the same
    # pipecat ReceptionistPipeline, differing only in transport/serializer.
    # See api/test_session_ws.py and api/telephony_webhooks.py.
    from api.test_session_ws import router as test_session_ws_router
    # WebRTC audio path for the browser test session (bot audio over a media
    # track + transcripts over the data channel) — the proven Clinical-Triage
    # approach. See api/test_session_webrtc.py.
    from api.test_session_webrtc import router as test_session_webrtc_router

    prefix = settings.api_prefix
    app.include_router(auth_router, prefix=prefix)
    app.include_router(patients_router, prefix=prefix)
    app.include_router(doctors_router, prefix=prefix)
    app.include_router(appointments_router, prefix=prefix)
    app.include_router(calls_router, prefix=prefix)
    app.include_router(analytics_router, prefix=prefix)
    app.include_router(providers_router, prefix=prefix)
    app.include_router(provider_settings_router, prefix=prefix)
    app.include_router(language_router, prefix=prefix)
    app.include_router(clinic_router, prefix=prefix)
    app.include_router(health_router, prefix=prefix)
    app.include_router(scheduling_router, prefix=prefix)
    app.include_router(test_session_router, prefix=prefix)
    app.include_router(test_session_ws_router, prefix=prefix)
    app.include_router(test_session_webrtc_router, prefix=prefix)


_include_routers()


# ---------------------------------------------------------------------------
# Telephony webhook routers (added in Phase 8.4)
# ---------------------------------------------------------------------------

try:
    from api.telephony_webhooks import router as telephony_router
    app.include_router(telephony_router, prefix=settings.api_prefix)
except ImportError:
    pass  # Built in Phase 8.4


# ---------------------------------------------------------------------------
# WebSocket endpoint (live call monitor)
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws_manager.connect(ws)
    try:
        while True:
            # Keep connection alive; client can send pings
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Static files and templates (Phase 8.6)
# ---------------------------------------------------------------------------

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------------------------------------------------------
# UI routes — serve Jinja2 templates
# ---------------------------------------------------------------------------

try:
    templates = Jinja2Templates(directory="templates")

    from fastapi import Request
    from fastapi.responses import HTMLResponse

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse(request, "dashboard.html")

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html")

    @app.get("/calls/live", response_class=HTMLResponse)
    async def live_calls(request: Request):
        return templates.TemplateResponse(request, "calls_live.html")

    @app.get("/test-session", response_class=HTMLResponse)
    async def test_session_page(request: Request):
        # no-store so the browser always fetches the latest inline JS — stale
        # cached JS was silently breaking the realtime call (double-connect).
        resp = templates.TemplateResponse(request, "test_session.html")
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp

    @app.get("/calls/history", response_class=HTMLResponse)
    async def call_history(request: Request):
        return templates.TemplateResponse(request, "calls_history.html")

    @app.get("/appointments", response_class=HTMLResponse)
    async def appointments_page(request: Request):
        return templates.TemplateResponse(request, "appointments.html")

    @app.get("/patients", response_class=HTMLResponse)
    async def patients_page(request: Request):
        return templates.TemplateResponse(request, "patients.html")

    @app.get("/doctors", response_class=HTMLResponse)
    async def doctors_page(request: Request):
        return templates.TemplateResponse(request, "doctors.html")

    @app.get("/doctors/{doctor_id}/availability", response_class=HTMLResponse)
    async def doctor_availability(request: Request, doctor_id: int):
        return templates.TemplateResponse(request, "doctor_availability.html", {"doctor_id": doctor_id})

    @app.get("/analytics", response_class=HTMLResponse)
    async def analytics_page(request: Request):
        return templates.TemplateResponse(request, "analytics.html")

    @app.get("/settings/providers", response_class=HTMLResponse)
    async def settings_providers(request: Request):
        return templates.TemplateResponse(request, "settings_providers.html")

    @app.get("/settings/language", response_class=HTMLResponse)
    async def settings_language(request: Request):
        return templates.TemplateResponse(request, "settings_language.html")

    @app.get("/settings/clinic", response_class=HTMLResponse)
    async def settings_clinic(request: Request):
        return templates.TemplateResponse(request, "settings_clinic.html")

    @app.get("/settings/users", response_class=HTMLResponse)
    async def settings_users(request: Request):
        return templates.TemplateResponse(request, "settings_users.html")

    @app.get("/settings/health", response_class=HTMLResponse)
    async def settings_health(request: Request):
        return templates.TemplateResponse(request, "settings_health.html")

except Exception:
    pass  # Templates built in Phase 8.6


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        # CRITICAL: never watch runtime-write dirs. The TTS cache writes a .wav
        # on every synthesis; without these excludes watchfiles reloads the
        # server mid-call, dropping the WebSocket and killing the live pipeline.
        reload_excludes=[
            "tts_cache/*", "tts_cache/**/*", "*.wav",
            "static/*", "*.log", "__pycache__/*",
        ],
        log_level="debug" if settings.debug else "info",
    )
