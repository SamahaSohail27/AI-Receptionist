"""
Booking-tool bridge — registers the clinic booking tools onto a pipecat LLM.

The actual tool implementations already exist and are battle-tested in
``api/test_session.py`` (``_TOOL_DISPATCH`` + ``_TOOLS`` + ``_system_prompt``).
Rather than duplicate them, this module adapts each one to pipecat's
function-calling convention so the live voice pipeline books appointments,
searches doctors, triages, reschedules and cancels — exactly like the REST
test flow does, just driven by speech.

pipecat handler convention (0.0.85):
    async def handler(params: FunctionCallParams):
        ...
        await params.result_callback(result_dict)

Each tool opens its own short-lived DB session (the pipeline has no request
scope), runs the existing async tool function, and returns its dict result.
"""
from __future__ import annotations

import logging
from typing import Any

from core.database import AsyncSessionLocal

# Reuse the exact tool suite + schemas + prompt the REST test flow uses.
from api.test_session import _TOOLS, _TOOL_DISPATCH, _system_prompt

logger = logging.getLogger(__name__)


def booking_tools_schema() -> list[dict[str, Any]]:
    """OpenAI-format tool schemas to attach to the LLM context."""
    return _TOOLS


def booking_system_prompt(language: str) -> str:
    """Rich, tool-aware receptionist prompt (Amina) for the given language."""
    return _system_prompt(language)


def register_booking_tools(llm, session_id: str) -> None:
    """Register every booking tool on the pipecat LLM service.

    Args:
        llm: the pipecat LLMService (OpenAI/Groq/Anthropic) for this session.
        session_id: used by book_appointment to stamp the CallLog/appointment.
    """

    def _make_handler(name: str, fn):
        async def handler(params) -> None:  # params: FunctionCallParams
            args = dict(params.arguments or {})
            try:
                async with AsyncSessionLocal() as db:
                    if name == "book_appointment":
                        result = await fn(db, session_id=session_id, **args)
                    else:
                        result = await fn(db, **args)
            except Exception as exc:  # never let a tool crash the turn
                logger.exception("voice tool %s failed: %s", name, exc)
                result = {"error": str(exc)}
            logger.info("voice tool %s(%s) -> %s", name, args, str(result)[:200])
            await params.result_callback(result)
        return handler

    for tool_name, fn in _TOOL_DISPATCH.items():
        llm.register_function(tool_name, _make_handler(tool_name, fn))

    logger.info("registered %d booking tools on LLM", len(_TOOL_DISPATCH))
