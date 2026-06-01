"""
Shared fixtures for the entire test suite.
"""
from __future__ import annotations

import asyncio
import os
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# -------------------------------------------------------------------------
# Ensure app can find modules regardless of working directory
# -------------------------------------------------------------------------
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# -------------------------------------------------------------------------
# Minimal env so pydantic-settings doesn't fail on missing keys
# -------------------------------------------------------------------------
os.environ.setdefault("OPENAI_API_KEY", "sk-test-00000000000000000000000000000000")
os.environ.setdefault("DEEPGRAM_API_KEY", "dg_test_00000000000000000000000000000000")
os.environ.setdefault("AZURE_SPEECH_KEY", "azure_test_key")
os.environ.setdefault("AZURE_SPEECH_REGION", "eastus")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
os.environ.setdefault("PLIVO_AUTH_ID", "test_auth_id")
os.environ.setdefault("PLIVO_AUTH_TOKEN", "test_auth_token")
os.environ.setdefault("GROQ_API_KEY", "gsk_test_00000000000000000000000000000000")


# -------------------------------------------------------------------------
# FastAPI test client fixture (integration tests)
# -------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app():
    """Return FastAPI app instance with DB/Redis overridden by mocks."""
    from unittest.mock import patch, AsyncMock, MagicMock

    mock_db_session = AsyncMock()
    mock_redis = AsyncMock()

    with patch("core.database.get_db", return_value=mock_db_session), \
         patch("core.database.engine", MagicMock()), \
         patch("redis.asyncio.from_url", return_value=mock_redis):
        from main import app as fastapi_app
        return fastapi_app


@pytest.fixture
def client(app):
    from httpx import AsyncClient
    return AsyncClient(app=app, base_url="http://test")


@pytest.fixture
def mock_redis():
    """Async Redis mock for unit tests involving BookingService."""
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock(return_value=1)
    redis.eval = AsyncMock(return_value=1)
    return redis


@pytest.fixture
def mock_db():
    """AsyncSession mock."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.close = AsyncMock()
    return db


@pytest.fixture
def auth_headers():
    """JWT header for admin user."""
    from core.auth import create_access_token
    token = create_access_token(1, "admin@clinic.pk", "admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def doctor_headers():
    """JWT header for doctor user."""
    from core.auth import create_access_token
    token = create_access_token(2, "doctor@clinic.pk", "doctor")
    return {"Authorization": f"Bearer {token}"}
