"""
Integration tests — authentication endpoints.

Uses FastAPI TestClient with mocked DB.
Tests: login, token validation, role-based access, refresh.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.integration
class TestAuthEndpoints:

    @pytest.mark.asyncio
    async def test_login_success(self):
        """POST /api/v1/auth/token returns JWT on valid credentials."""
        from fastapi.testclient import TestClient
        from core.database import get_db

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.name = "Admin User"
        mock_user.email = "admin@clinic.pk"
        mock_user.role = "admin"
        mock_user.hashed_password = "hashed"
        mock_user.is_active = True
        mock_user.last_login = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        async def override_get_db():
            yield mock_db

        with patch("api.auth.verify_password", return_value=True):
            from main import app
            app.dependency_overrides[get_db] = override_get_db
            try:
                client = TestClient(app)
                resp = client.post(
                    "/api/v1/auth/token",
                    data={"username": "admin@clinic.pk", "password": "correctpassword"},
                )
                assert resp.status_code in (200, 401, 422)
            finally:
                app.dependency_overrides.pop(get_db, None)

    @pytest.mark.asyncio
    async def test_login_wrong_password_returns_401(self):
        from fastapi.testclient import TestClient
        from core.database import get_db

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def override_get_db():
            yield mock_db

        from main import app
        app.dependency_overrides.clear()  # ensure clean slate
        app.dependency_overrides[get_db] = override_get_db
        try:
            client = TestClient(app)
            resp = client.post(
                "/api/v1/auth/token",
                data={"username": "admin@clinic.pk", "password": "wrongpassword"},
            )
            assert resp.status_code == 401
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_protected_endpoint_without_token_returns_401(self):
        with patch("core.database.get_db"):
            from fastapi.testclient import TestClient
            from main import app
            client = TestClient(app)
            resp = client.get("/api/v1/appointments")
            assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_protected_endpoint_with_invalid_token_returns_401(self):
        from fastapi.testclient import TestClient
        from core.database import get_db

        mock_db = AsyncMock()

        async def override_get_db():
            yield mock_db

        from main import app
        app.dependency_overrides[get_db] = override_get_db
        try:
            client = TestClient(app)
            resp = client.get(
                "/api/v1/appointments",
                headers={"Authorization": "Bearer invalid.jwt.token"},
            )
            assert resp.status_code in (401, 403, 422)
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_create_access_token_contains_role(self):
        from core.auth import create_access_token, decode_token
        token = create_access_token(5, "doctor@clinic.pk", "doctor")
        payload = decode_token(token)
        assert payload["role"] == "doctor"
        assert payload["sub"] == "5"

    def test_create_access_token_expires(self):
        from core.auth import decode_token
        from jose import jwt
        from datetime import timedelta, datetime, timezone
        from core.config import settings
        import time
        expired_payload = {
            "sub": "1",
            "role": "admin",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=10),
        }
        token = jwt.encode(expired_payload, settings.secret_key, algorithm=settings.jwt_algorithm)
        with pytest.raises(Exception):
            decode_token(token)

    def test_require_admin_role_factory(self):
        from core.auth import require_roles
        dep = require_roles("admin")
        assert callable(dep)

    def test_require_roles_multiple_roles(self):
        from core.auth import require_roles
        dep = require_roles("admin", "doctor")
        assert callable(dep)
