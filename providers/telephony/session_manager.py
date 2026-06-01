from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any


class TelephonySessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def register_session(self, session_id: str, metadata: dict) -> None:
        async with self._lock:
            self._sessions[session_id] = {
                **metadata,
                "registered_at": datetime.utcnow().isoformat(),
            }

    async def get_session(self, session_id: str) -> dict | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def update_session(self, session_id: str, updates: dict) -> None:
        async with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].update(updates)

    async def end_session(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def get_active_count(self) -> int:
        async with self._lock:
            return len(self._sessions)

    async def get_all_active(self) -> list[dict]:
        async with self._lock:
            return list(self._sessions.values())


session_manager = TelephonySessionManager()
