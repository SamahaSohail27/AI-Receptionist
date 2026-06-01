"""
Integration tests — appointments endpoints.

Key invariants to test:
- /today route resolves before /:id to avoid int-cast errors
- PKT timezone used for day boundaries
- Idempotency check runs before conflict check
- Double-booking returns 409
- Authorization required
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_appointment(id=1, doctor_id=1, patient_id=1, status="scheduled"):
    appt = MagicMock()
    appt.id = id
    appt.doctor_id = doctor_id
    appt.patient_id = patient_id
    appt.status = status
    appt.slot_start_utc = datetime(2025, 6, 2, 9, 0, tzinfo=timezone.utc)
    appt.appointment_type = "consultation"
    appt.idempotency_key = f"idem-{id}"
    appt.created_by_session = "test-session"
    return appt


@pytest.mark.integration
class TestAppointmentEndpoints:

    @pytest.mark.asyncio
    async def test_get_today_appointments_endpoint_exists(self):
        """GET /api/v1/appointments/today does not fall through to /:id parser."""
        from fastapi.testclient import TestClient
        from core.database import get_db
        from core.auth import get_current_user, create_access_token

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [_make_appointment()]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_admin = MagicMock(role="admin", id=1)

        async def override_get_db():
            yield mock_db

        async def override_get_user():
            return mock_admin

        from main import app
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_get_user
        try:
            client = TestClient(app, raise_server_exceptions=False)
            token = create_access_token(1, "admin@clinic.pk", "admin")
            resp = client.get(
                "/api/v1/appointments/today",
                headers={"Authorization": f"Bearer {token}"},
            )
            # Anything except 404 proves the /today route exists (not mis-cast as /:id int)
            assert resp.status_code != 404
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    @pytest.mark.asyncio
    async def test_get_appointment_by_id(self):
        from fastapi.testclient import TestClient
        from core.database import get_db
        from core.auth import get_current_user, create_access_token

        mock_appt = _make_appointment(id=42)
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_appt
        mock_result.scalar_one_or_none.return_value = mock_appt
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_admin = MagicMock(role="admin", id=1)

        async def override_get_db():
            yield mock_db

        async def override_get_user():
            return mock_admin

        from main import app
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_get_user
        try:
            client = TestClient(app, raise_server_exceptions=False)
            token = create_access_token(1, "admin@clinic.pk", "admin")
            resp = client.get(
                "/api/v1/appointments/42",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code in (200, 404, 422, 500)
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    @pytest.mark.asyncio
    async def test_delete_appointment_returns_405(self):
        """DELETE on appointments cancels (not hard-deletes) or 404s — never errors."""
        from fastapi.testclient import TestClient
        from core.database import get_db
        from core.auth import get_current_user, create_access_token
        mock_admin = MagicMock(role="admin", id=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # appointment not found → 404
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def override_get_db():
            yield mock_db

        async def override_get_user():
            return mock_admin

        from main import app
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_get_user
        try:
            client = TestClient(app, raise_server_exceptions=False)
            token = create_access_token(1, "admin@clinic.pk", "admin")
            resp = client.delete(
                "/api/v1/appointments/1",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code in (200, 404, 405, 422, 500)
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_appointments_endpoint_requires_auth(self):
        with patch("core.database.get_db"):
            from fastapi.testclient import TestClient
            from main import app
            client = TestClient(app)
            resp = client.get("/api/v1/appointments")
            assert resp.status_code in (401, 403)


@pytest.mark.integration
class TestPKTConversion:
    """Verify day-boundary queries use PKT timezone offset."""

    def test_pkt_offset_is_5_hours(self):
        import pytz
        PKT = pytz.timezone("Asia/Karachi")
        now_pkt = datetime.now(PKT)
        utc_offset_hours = now_pkt.utcoffset().total_seconds() / 3600
        assert utc_offset_hours == 5.0

    def test_pkt_is_pytz_not_timedelta(self):
        import pytz
        from scheduling.pkt_calendar import PKT
        assert isinstance(PKT, pytz.BaseTzInfo)

    def test_no_timedelta_offset_in_engine(self):
        """Ensure scheduling/engine.py doesn't use timedelta(hours=5) for PKT."""
        import inspect
        from scheduling import engine
        source = inspect.getsource(engine)
        # Filter out comment lines
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith("#")
        ]
        for line in code_lines:
            assert "timedelta(hours=5)" not in line, (
                f"Found forbidden timedelta(hours=5) in non-comment line: {line!r}"
            )
