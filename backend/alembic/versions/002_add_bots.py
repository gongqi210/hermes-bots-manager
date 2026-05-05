"""add bots table

Revision ID: 002_add_bots
Revises: 001
Create Date: 2026-04-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "002_add_bots"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("feishu_app_id", sa.String(length=64), nullable=True),
        sa.Column("feishu_app_secret_enc", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(length=64), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_bots_name"),
    )
    # Partial unique index — only enforces uniqueness when feishu_app_id is set.
    # Half-configured Bots (filesystem profile exists, .env not yet provided) all
    # have NULL feishu_app_id and must coexist; once a Secret is bound, no two
    # Bots may share the same App ID (FEISHU-05 prep — Phase 3 binds App ID).
    op.create_index(
        "uq_bots_feishu_app_id_when_set",
        "bots",
        ["feishu_app_id"],
        unique=True,
        sqlite_where=sa.text("feishu_app_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_bots_feishu_app_id_when_set", table_name="bots")
    op.drop_table("bots")
