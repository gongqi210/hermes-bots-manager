"""add feishu_user_id to users (Phase 5 plan 05-05 — allowlist presets)

Revision ID: 006
Revises: 005
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("feishu_user_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "feishu_user_id")
