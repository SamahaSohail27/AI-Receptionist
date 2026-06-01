"""Initial schema — all tables

Revision ID: 0001
Revises:
Create Date: 2026-04-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "clinic_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("clinic_name", sa.String(200), nullable=False),
        sa.Column("clinic_name_ur", sa.String(200)),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="Asia/Karachi"),
        sa.Column("default_language", sa.String(10), nullable=False, server_default="ur-PK"),
        sa.Column("dtmf_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("whatsapp_reminders_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("sms_fallback_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("reminder_hours_before", sa.String(50), nullable=False, server_default="24,2"),
        sa.Column("slot_lock_seconds", sa.Integer, nullable=False, server_default="45"),
        sa.Column("max_concurrent_calls", sa.Integer, nullable=False, server_default="20"),
        sa.Column("business_hours", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("holidays", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("logo_url", sa.String(500)),
        sa.Column("address", sa.Text),
        sa.Column("phone_display", sa.String(30)),
        sa.Column("triage_nurse_number", sa.String(20)),
        sa.Column("after_hours_number", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "provider_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("clinic_id", sa.Integer, nullable=False),
        sa.Column("telephony_primary", sa.String(20), nullable=False, server_default="plivo"),
        sa.Column("telephony_fallback", sa.String(20), nullable=False, server_default="twilio"),
        sa.Column("language_providers", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("stt_confidence_ur", sa.Float, nullable=False, server_default="0.45"),
        sa.Column("stt_confidence_en", sa.Float, nullable=False, server_default="0.70"),
        sa.Column("stt_confidence_pa", sa.Float, nullable=False, server_default="0.40"),
        sa.Column("llm_model_standard", sa.String(100), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("llm_model_quality", sa.String(100), nullable=False, server_default="gpt-4o"),
        sa.Column("llm_temperature", sa.Float, nullable=False, server_default="0.3"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "patients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cnic_hash", sa.String(64)),
        sa.Column("phone_e164", sa.String(20), nullable=False),
        sa.Column("name_en", sa.String(200)),
        sa.Column("name_ur", sa.String(200)),
        sa.Column("birth_year", sa.Integer),
        sa.Column("gender", sa.String(1)),
        sa.Column("preferred_language", sa.String(10), nullable=False, server_default="ur-PK"),
        sa.Column("whatsapp_opted_in", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("whatsapp_consent_at", sa.String(30)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_patients_cnic_hash", "patients", ["cnic_hash"], unique=True)
    op.create_index("ix_patients_phone", "patients", ["phone_e164"])

    op.create_table(
        "doctors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name_en", sa.String(200), nullable=False),
        sa.Column("name_ur", sa.String(200)),
        sa.Column("speciality", sa.String(100), nullable=False),
        sa.Column("speciality_ur", sa.String(100)),
        sa.Column("department", sa.String(100), nullable=False),
        sa.Column("room_number", sa.String(20)),
        sa.Column("consultation_fee", sa.Integer, nullable=False, server_default="500"),
        sa.Column("consultation_duration_minutes", sa.Integer, nullable=False, server_default="20"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("preferred_language", sa.String(10), nullable=False, server_default="ur-PK"),
        sa.Column("calendar_color", sa.String(7), nullable=False, server_default="#0EA5E9"),
        sa.Column("insurance_panels", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_doctors_speciality", "doctors", ["speciality"])

    op.create_table(
        "doctor_availability",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("doctor_id", sa.Integer, nullable=False),
        sa.Column("day_of_week", sa.Integer, nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("end_time", sa.String(5), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_availability_doctor_day", "doctor_availability", ["doctor_id", "day_of_week"])

    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("patient_id", sa.Integer, nullable=False),
        sa.Column("doctor_id", sa.Integer, nullable=False),
        sa.Column("slot_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slot_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("appointment_type", sa.String(30), nullable=False, server_default="consultation"),
        sa.Column("booking_source", sa.String(10), nullable=False, server_default="ai"),
        sa.Column("call_log_id", sa.Integer),
        sa.Column("idempotency_key", sa.String(100)),
        sa.Column("notes", sa.Text),
        sa.Column("reminder_24h_sent", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("reminder_2h_sent", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("whatsapp_confirmation_sent", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_by", sa.String(10)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"]),
        sa.UniqueConstraint("doctor_id", "slot_start_utc", name="uq_appointment_doctor_slot"),
        sa.UniqueConstraint("idempotency_key", name="uq_appointment_idempotency"),
    )
    op.create_index("ix_appointments_patient", "appointments", ["patient_id"])
    op.create_index("ix_appointments_doctor_date", "appointments", ["doctor_id", "slot_start_utc"])
    op.create_index("ix_appointments_status", "appointments", ["status"])

    op.create_table(
        "slot_reservations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("doctor_id", sa.Integer, nullable=False),
        sa.Column("slot_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("doctor_id", "slot_start_utc", name="uq_reservation_doctor_slot"),
    )

    op.create_table(
        "call_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("patient_id", sa.Integer),
        sa.Column("caller_phone_hash", sa.String(64)),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("intent", sa.String(50)),
        sa.Column("outcome", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("telephony_provider", sa.String(20), nullable=False),
        sa.Column("stt_provider", sa.String(20), nullable=False),
        sa.Column("llm_provider", sa.String(20), nullable=False),
        sa.Column("tts_provider", sa.String(20), nullable=False),
        sa.Column("llm_model", sa.String(100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Integer),
        sa.Column("turn_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_stt_ms", sa.Integer),
        sa.Column("avg_llm_ms", sa.Integer),
        sa.Column("avg_tts_ms", sa.Integer),
        sa.Column("avg_total_ms", sa.Integer),
        sa.Column("p95_total_ms", sa.Integer),
        sa.Column("tts_cache_hits", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tts_cache_misses", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_stt_usd", sa.Float),
        sa.Column("cost_llm_usd", sa.Float),
        sa.Column("cost_tts_usd", sa.Float),
        sa.Column("cost_telephony_usd", sa.Float),
        sa.Column("cost_total_usd", sa.Float),
        sa.Column("escalation_triggered", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("escalation_reason", sa.String(50)),
        sa.Column("emergency_detected", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("appointment_id", sa.Integer),
        sa.Column("turn_metrics", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_call_logs_session", "call_logs", ["session_id"], unique=True)
    op.create_index("ix_call_logs_patient", "call_logs", ["patient_id"])
    op.create_index("ix_call_logs_created", "call_logs", ["created_at"])
    op.create_index("ix_call_logs_outcome", "call_logs", ["outcome"])
    op.create_index("ix_call_logs_language", "call_logs", ["language"])

    op.create_table(
        "transcripts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("call_log_id", sa.Integer, nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("turn_id", sa.Integer, nullable=False),
        sa.Column("speaker", sa.String(10), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("is_rtl", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("masked_text", sa.Text),
        sa.Column("stt_confidence", sa.Float),
        sa.Column("stt_provider", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["call_log_id"], ["call_logs.id"]),
    )
    op.create_index("ix_transcripts_call", "transcripts", ["call_log_id"])
    op.create_index("ix_transcripts_session", "transcripts", ["session_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("hashed_password", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="receptionist"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_login", sa.DateTime(timezone=True)),
        sa.Column("doctor_id", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Integer),
        sa.Column("user_email", sa.String(200)),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("session_id", sa.String(36)),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50)),
        sa.Column("entity_id", sa.Integer),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("notes", sa.String(500)),
    )
    op.create_index("ix_audit_user", "audit_log", ["user_id"])
    op.create_index("ix_audit_entity", "audit_log", ["entity_type", "entity_id"])
    op.create_index("ix_audit_created", "audit_log", ["created_at"])

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("appointment_id", sa.Integer, nullable=False),
        sa.Column("patient_id", sa.Integer, nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("notification_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(500)),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notif_appointment", "notification_log", ["appointment_id"])
    op.create_index("ix_notif_status", "notification_log", ["status"])


def downgrade() -> None:
    for table in [
        "notification_log", "audit_log", "users", "transcripts", "call_logs",
        "slot_reservations", "appointments", "doctor_availability", "doctors",
        "patients", "provider_config", "clinic_config",
    ]:
        op.drop_table(table)
