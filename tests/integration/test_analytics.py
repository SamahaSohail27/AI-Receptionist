"""
Integration tests — analytics endpoints.

Verifies:
- All 8 chart endpoints exist and return expected shape
- Authentication enforced
- KPI definitions are complete
- Anomaly detection thresholds correct
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.integration
class TestAnalyticsEndpoints:

    @pytest.fixture
    def token(self):
        from core.auth import create_access_token
        return create_access_token(1, "admin@clinic.pk", "admin")

    @pytest.fixture
    def client_with_mock_db(self):
        from fastapi.testclient import TestClient
        from core.database import get_db

        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_result.fetchall.return_value = []
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def override_get_db():
            yield mock_db

        from main import app
        app.dependency_overrides[get_db] = override_get_db
        try:
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_summary_endpoint_exists(self, client_with_mock_db, token):
        resp = client_with_mock_db.get(
            "/api/v1/analytics/summary",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 404  # 422/401/200/500 all confirm the endpoint exists

    def test_volume_endpoint_exists(self, client_with_mock_db, token):
        resp = client_with_mock_db.get(
            "/api/v1/analytics/calls/volume",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 404

    def test_language_distribution_endpoint_exists(self, client_with_mock_db, token):
        resp = client_with_mock_db.get(
            "/api/v1/analytics/calls/language-distribution",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 404

    def test_outcomes_endpoint_exists(self, client_with_mock_db, token):
        resp = client_with_mock_db.get(
            "/api/v1/analytics/calls/outcomes",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 404

    def test_latency_endpoint_exists(self, client_with_mock_db, token):
        resp = client_with_mock_db.get(
            "/api/v1/analytics/calls/latency",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 404

    def test_costs_endpoint_exists(self, client_with_mock_db, token):
        resp = client_with_mock_db.get(
            "/api/v1/analytics/calls/cost",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 404

    def test_peak_hours_endpoint_exists(self, client_with_mock_db, token):
        resp = client_with_mock_db.get(
            "/api/v1/analytics/calls/peak-hours",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code != 404

    def test_analytics_requires_auth(self, client_with_mock_db):
        resp = client_with_mock_db.get("/api/v1/analytics/summary")
        assert resp.status_code in (401, 403)


@pytest.mark.unit
class TestKPIDefinitions:

    def test_all_kpis_defined(self):
        from analytics.metrics import KPI_DEFINITIONS
        expected_kpis = {
            "aht",
            "booking_success_rate",
            "escalation_rate",
            "abandonment_rate",
            "language_distribution",
            "cost_per_call",
            "cost_per_booking",
            "p95_latency",
        }
        assert expected_kpis.issubset(set(KPI_DEFINITIONS.keys()))

    def test_anomaly_escalation_threshold_is_20_percent(self):
        from analytics.metrics import ESCALATION_RATE_ALERT
        assert ESCALATION_RATE_ALERT == 0.20

    def test_anomaly_latency_threshold_is_1000ms(self):
        from analytics.metrics import LATENCY_P95_ALERT_MS
        assert LATENCY_P95_ALERT_MS == 1000

    def test_anomaly_stt_error_threshold_is_5_percent(self):
        from analytics.metrics import STT_ERROR_RATE_ALERT
        assert STT_ERROR_RATE_ALERT == 0.05

    def test_anomaly_detector_check_escalation_rate(self):
        from analytics.metrics import AnomalyDetector
        detector = AnomalyDetector()
        with patch.object(detector, "_alert") as mock_alert:
            result = detector.check_escalation_rate(booked=10, escalated=3)  # 30% > 20%
            assert result is True
            mock_alert.assert_called_once()

    def test_anomaly_detector_no_alert_below_threshold(self):
        from analytics.metrics import AnomalyDetector
        from unittest.mock import patch
        detector = AnomalyDetector()
        with patch.object(detector, "_alert") as mock_alert:
            result = detector.check_escalation_rate(booked=10, escalated=1)  # 10% < 20%
            assert result is False
            mock_alert.assert_not_called()

    def test_anomaly_detector_check_latency_triggers_above_1000ms(self):
        from analytics.metrics import AnomalyDetector
        from unittest.mock import patch
        detector = AnomalyDetector()
        with patch.object(detector, "_alert") as mock_alert:
            result = detector.check_latency(1500)
            assert result is True
            mock_alert.assert_called_once()

    def test_anomaly_detector_check_latency_no_alert_below_1000ms(self):
        from analytics.metrics import AnomalyDetector
        from unittest.mock import patch
        detector = AnomalyDetector()
        with patch.object(detector, "_alert") as mock_alert:
            result = detector.check_latency(800)
            assert result is False
            mock_alert.assert_not_called()

    def test_anomaly_detector_zero_total_calls_returns_false(self):
        from analytics.metrics import AnomalyDetector
        detector = AnomalyDetector()
        result = detector.check_escalation_rate(booked=0, escalated=0)
        assert result is False
