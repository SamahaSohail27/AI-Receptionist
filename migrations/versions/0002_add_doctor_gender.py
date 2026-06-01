"""Add gender column to doctors

Revision ID: 0002_doctor_gender
Revises: 0001_initial
Create Date: 2026-04-30

Used by the search_doctors tool to satisfy patient gender preferences
("book with a female doctor"). Nullable so existing rows are unaffected.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_doctor_gender"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "doctors",
        sa.Column("gender", sa.String(1), nullable=True),
    )
    op.create_index("ix_doctors_gender", "doctors", ["gender"])


def downgrade() -> None:
    op.drop_index("ix_doctors_gender", table_name="doctors")
    op.drop_column("doctors", "gender")
