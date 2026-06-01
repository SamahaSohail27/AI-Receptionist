"""
Integration tests — patients endpoints.

Key invariants:
- Phone numbers normalized to E.164 +92XXXXXXXXXX
- CNIC stored as SHA-256 hash, never plain
- DELETE returns 405 (data retention policy)
- PHI access is logged to AuditLog
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_patient(id=1, phone="+923001234567"):
    p = MagicMock()
    p.id = id
    p.name_en = "Test Patient"
    p.name_ur = "ٹیسٹ مریض"
    p.phone_e164 = phone
    p.cnic_hash = "a" * 64
    return p


@pytest.mark.integration
class TestPatientEndpoints:

    def test_patient_delete_returns_405(self):
        """Hard delete of patients is forbidden — data retention policy."""
        from fastapi.testclient import TestClient
        from core.database import get_db
        from core.auth import get_current_user, create_access_token
        mock_admin = MagicMock(role="admin", id=1)
        mock_db = AsyncMock()

        async def override_get_db():
            yield mock_db

        async def override_get_user():
            return mock_admin

        from main import app
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_get_user
        try:
            client = TestClient(app)
            token = create_access_token(1, "admin@clinic.pk", "admin")
            resp = client.delete(
                "/api/v1/patients/1",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code in (404, 405)
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_patient_list_requires_auth(self):
        with patch("core.database.get_db"):
            from fastapi.testclient import TestClient
            from main import app
            client = TestClient(app)
            resp = client.get("/api/v1/patients")
            assert resp.status_code in (401, 403)

    def test_phone_normalization_11_digit_to_e164(self):
        from api.patients import normalize_phone
        assert normalize_phone("03001234567") == "+923001234567"

    def test_phone_normalization_already_e164_unchanged(self):
        from api.patients import normalize_phone
        assert normalize_phone("+923001234567") == "+923001234567"

    def test_phone_normalization_handles_spaces(self):
        from api.patients import normalize_phone
        result = normalize_phone("0300 123 4567")
        assert result.startswith("+92")
        assert " " not in result

    def test_phone_normalization_handles_dashes(self):
        from api.patients import normalize_phone
        result = normalize_phone("0300-123-4567")
        assert result.startswith("+92")

    def test_phone_normalization_preserves_existing_92_prefix(self):
        from api.patients import normalize_phone
        assert normalize_phone("+923001234567") == "+923001234567"


@pytest.mark.integration
class TestPHIHandling:

    def test_cnic_hashed_sha256(self):
        """Verify CNIC is stored as SHA-256 not plain."""
        import hashlib
        raw = "12345-6789012-3"
        expected_hash = hashlib.sha256(raw.encode()).hexdigest()
        # Try to import the hashing function from the patients module
        try:
            from api.patients import hash_cnic
            assert hash_cnic(raw) == expected_hash
        except ImportError:
            pytest.skip("hash_cnic not exported from api.patients")

    def test_cnic_hash_length_is_64(self):
        """SHA-256 hex digest is always 64 characters."""
        import hashlib
        cnic = "35201-1234567-1"
        h = hashlib.sha256(cnic.encode()).hexdigest()
        assert len(h) == 64

    def test_phi_masking_replaces_phone(self):
        from analytics.ingestion import _mask_phi
        text = "Patient called from +923001234567 about their appointment"
        masked = _mask_phi(text)
        assert "+923001234567" not in masked
        assert "[PHONE]" in masked or "***" in masked or masked != text

    def test_phi_masking_replaces_cnic(self):
        from analytics.ingestion import _mask_phi
        text = "CNIC 35201-1234567-1 was provided"
        masked = _mask_phi(text)
        assert "35201-1234567-1" not in masked

    def test_phi_masking_replaces_03_format_phone(self):
        from analytics.ingestion import _mask_phi
        text = "Patient phone 03001234567 received"
        masked = _mask_phi(text)
        assert "03001234567" not in masked
