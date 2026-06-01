"""
KPI definitions and anomaly detection.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Anomaly thresholds
ESCALATION_RATE_ALERT = 0.20    # 20% escalation rate triggers alert
LATENCY_P95_ALERT_MS = 1000     # p95 > 1000ms triggers alert
STT_ERROR_RATE_ALERT = 0.05     # >5% STT failures triggers alert


class AnomalyDetector:
    """Detects anomalies in call metrics and broadcasts alerts."""

    def __init__(self) -> None:
        self._last_check: dict[str, datetime] = {}

    def check_escalation_rate(self, booked: int, escalated: int) -> bool:
        total = booked + escalated
        if total == 0:
            return False
        rate = escalated / total
        if rate > ESCALATION_RATE_ALERT:
            self._alert("high_escalation_rate", {"rate": round(rate, 3), "threshold": ESCALATION_RATE_ALERT})
            return True
        return False

    def check_latency(self, p95_ms: int) -> bool:
        if p95_ms > LATENCY_P95_ALERT_MS:
            self._alert("high_latency", {"p95_ms": p95_ms, "threshold_ms": LATENCY_P95_ALERT_MS})
            return True
        return False

    def _alert(self, alert_type: str, data: dict) -> None:
        from core.ws_manager import ws_manager
        event = {"type": f"alert.{alert_type}", "data": data, "timestamp": datetime.now(timezone.utc).isoformat()}
        ws_manager.broadcast_fire_and_forget(event)
        logger.warning("Anomaly detected: %s %s", alert_type, data)


# KPI definitions
KPI_DEFINITIONS = {
    "aht": "Average Handle Time — mean call duration in seconds",
    "booking_success_rate": "Percentage of calls that result in a confirmed appointment",
    "escalation_rate": "Percentage of calls transferred to human",
    "abandonment_rate": "Percentage of calls ended without outcome",
    "language_distribution": "Percentage breakdown of ur-PK / pa-PK / en calls",
    "cost_per_call": "Average total cost per call in USD",
    "cost_per_booking": "Average total cost per successful booking in USD",
    "p95_latency": "95th percentile end-to-end voice turn latency",
}
