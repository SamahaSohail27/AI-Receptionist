from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import require_admin
from core.config import settings
from core.database import get_db
from providers.telephony.session_manager import session_manager

router = APIRouter(prefix="/health", tags=["health"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
async def liveness() -> dict:
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@router.get("/providers")
async def detailed_health(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user=Depends(require_admin),
) -> dict:
    # --- Database ---
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    # --- Redis ---
    redis_status = "ok"
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
    except Exception:
        redis_status = "error"

    # --- Provider API keys (presence only — never return key values) ---
    providers = {
        "deepgram": {"configured": len(settings.deepgram_api_key) > 0},
        "openai": {"configured": len(settings.openai_api_key) > 0},
        "anthropic": {"configured": len(settings.anthropic_api_key) > 0},
        "azure_speech": {"configured": len(settings.azure_speech_key) > 0},
        "groq": {"configured": len(settings.groq_api_key) > 0},
        "plivo": {
            "configured": (
                len(settings.plivo_auth_id) > 0
                and len(settings.plivo_auth_token) > 0
            )
        },
        "twilio": {
            "configured": (
                len(settings.twilio_account_sid) > 0
                and len(settings.twilio_auth_token) > 0
            )
        },
        "elevenlabs": {"configured": len(settings.elevenlabs_api_key) > 0},
    }

    return {
        "database": db_status,
        "redis": redis_status,
        "providers": providers,
    }


@router.get("/pipeline")
async def pipeline_status(
    current_user=Depends(require_admin),
) -> dict:
    active_calls = await session_manager.get_active_count()
    # ws_connections reported by ws_manager if available, default 0 on import error
    try:
        from core.ws_manager import ws_manager
        ws_connections = ws_manager.connection_count
    except Exception:
        ws_connections = 0

    return {
        "active_calls": active_calls,
        "ws_connections": ws_connections,
    }
