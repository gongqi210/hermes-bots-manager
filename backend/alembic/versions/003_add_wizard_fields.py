"""add wizard fields to bots table (FEISHU-01)

Revision ID: 003_add_wizard_fields
Revises: 002_add_bots
Create Date: 2026-04-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003_add_wizard_fields"
down_revision = "002_add_bots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All three columns get server defaults so existing Phase 2 rows backfill
    # cleanly without nulls (model also enforces nullable=False).
    op.add_column(
        "bots",
        sa.Column("domain", sa.String(length=16), nullable=False, server_default="feishu"),
    )
    op.add_column(
        "bots",
        sa.Column(
            "connection_mode",
            sa.String(length=32),
            nullable=False,
            server_default="websocket",
        ),
    )
    op.add_column(
        "bots",
        sa.Column(
            "group_strategy",
            sa.String(length=16),
            nullable=False,
            server_default="mention",
        ),
    )


def downgrade() -> None:
    op.drop_column("bots", "group_strategy")
    op.drop_column("bots", "connection_mode")
    op.drop_column("bots", "domain")
