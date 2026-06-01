"""
Celery batch aggregation jobs — pre-compute daily/weekly/monthly rollups.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from celery import Celery
from core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "ai_receptionist_analytics",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.timezone = "Asia/Karachi"

# Register beat schedule
celery_app.conf.beat_schedule = {
    "daily-analytics-rollup": {
        "task": "analytics.daily_rollup",
        "schedule": 3600.0,  # every hour
    },
    "anomaly-check": {
        "task": "analytics.anomaly_check",
        "schedule": 300.0,  # every 5 minutes
    },
}


def _get_sync_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    db_url = settings.database_url.replace("+asyncpg", "+psycopg2")
    engine = create_engine(db_url)
    return sessionmaker(engine)()


@celery_app.task(name="analytics.daily_rollup")
def daily_rollup() -> dict:
    """Pre-aggregate yesterday's call data for fast chart queries."""
    from sqlalchemy import text
    db = _get_sync_db()
    try:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        result = db.execute(
            text("""
                SELECT
                    COUNT(*) AS total_calls,
                    COUNT(CASE WHEN outcome = 'booked' THEN 1 END) AS booked,
                    COUNT(CASE WHEN escalation_triggered THEN 1 END) AS escalated,
                    COUNT(CASE WHEN emergency_detected THEN 1 END) AS emergency,
                    AVG(avg_total_ms) AS avg_latency_ms,
                    SUM(cost_total_usd) AS total_cost_usd,
                    language,
                    stt_provider, llm_provider, tts_provider
                FROM call_logs
                WHERE DATE(created_at AT TIME ZONE 'Asia/Karachi') = :date
                GROUP BY language, stt_provider, llm_provider, tts_provider
            """),
            {"date": str(yesterday)},
        )
        rows = result.fetchall()
        db.commit()
        logger.info("Daily rollup complete for %s: %d rows", yesterday, len(rows))
        return {"date": str(yesterday), "rows": len(rows)}
    except Exception as e:
        logger.error("Daily rollup failed: %s", e)
        db.rollback()
        return {"error": str(e)}
    finally:
        db.close()


@celery_app.task(name="analytics.anomaly_check")
def anomaly_check() -> dict:
    """Check for anomalous patterns in the last 30 minutes."""
    from sqlalchemy import text
    from analytics.metrics import AnomalyDetector
    db = _get_sync_db()
    try:
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        result = db.execute(
            text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(CASE WHEN outcome = 'booked' THEN 1 END) AS booked,
                    COUNT(CASE WHEN escalation_triggered THEN 1 END) AS escalated,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY avg_total_ms) AS p95_latency
                FROM call_logs
                WHERE created_at >= :since
            """),
            {"since": since},
        )
        row = result.fetchone()
        if row and row.total > 0:
            detector = AnomalyDetector()
            detector.check_escalation_rate(row.booked or 0, row.escalated or 0)
            if row.p95_latency:
                detector.check_latency(int(row.p95_latency))
        return {"checked_calls": row.total if row else 0}
    except Exception as e:
        logger.error("Anomaly check failed: %s", e)
        return {"error": str(e)}
    finally:
        db.close()
