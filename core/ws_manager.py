"""
WebSocket connection manager for real-time UI updates.

ALL broadcasts are fire-and-forget — never awaited in the hot path.
Use: asyncio.create_task(ws_manager.broadcast_json(event))
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast_json(self, data: dict[str, Any]) -> None:
        """Broadcast JSON to all connected clients. Dead connections are silently removed."""
        payload = json.dumps(data, default=str)
        dead: list[WebSocket] = []
        async with self._lock:
            connections = set(self._connections)
        for ws in connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    def broadcast_fire_and_forget(self, data: dict[str, Any]) -> None:
        """Non-blocking broadcast — safe to call from sync context or hot path."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast_json(data))
        except RuntimeError:
            pass  # No event loop running — skip broadcast

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Module-level singleton
ws_manager = WebSocketManager()
