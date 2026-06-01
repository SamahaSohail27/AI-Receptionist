"""
Per-test engine disposal so the asyncpg pool isn't bound to a closed event loop.

pytest-asyncio creates a fresh loop per test (in auto mode); the module-level
AsyncSessionLocal in core/database.py keeps connections from the previous
loop, causing intermittent "loop is closed" errors that surface as skips
under our `_db_available()` guard. Disposing the engine at the end of each
test forces a fresh pool on the next test.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test():
    yield
    try:
        from core.database import engine
        await engine.dispose()
    except Exception:
        pass
