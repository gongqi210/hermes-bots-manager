"""add pairings + onboarding_run tables and Bot gateway-state columns (Phase 4)

Revision ID: 004
Revises: 003_add_wizard_fields
Create Date: 2026-05-04

Mirrors the SQLAlchemy column definitions in
:mod:`app.models.pairing`, :mod:`app.models.onboarding_run`, and the new
``gateway_state_cache`` / ``gateway_state_changed_at`` columns on
``app.models.bot``. CheckConstraints on ``status`` columns are baked into
``CREATE TABLE`` and the partial unique index for pairing dedupe matches the
ORM-level ``Index(..., sqlite_where=...)`` declaration.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003_add_wizard_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. pairings table — Phase 4 GATEWAY-10/11/12.
    op.create_table(
        "pairings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "bot_id",
            sa.Integer(),
            sa.ForeignKey("bots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "platform",
            sa.String(length=16),
            nullable=False,
            server_default="feishu",
        ),
        sa.Column("code_plaintext", sa.String(length=64), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_last4", sa.String(length=4), nullable=False),
        sa.Column("feishu_user_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("intercepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "processed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name="ck_pairings_status",
        ),
    )
    op.create_index("ix_pairings_bot_id", "pairings", ["bot_id"])
    op.create_index(
        "ix_pairings_status_expires", "pairings", ["status", "expires_at"]
    )
    # MAJOR 8: partial unique on (bot_id, code_hash) WHERE status='pending'.
    op.create_index(
        "ix_pairings_dedupe_pending",
        "pairings",
        ["bot_id", "code_hash"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
    )

    # 2. onboarding_run table — 3-minute KPI 埋点 (CONTEXT D-19).
    op.create_table(
        "onboarding_run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "bot_id",
            sa.Integer(),
            sa.ForeignKey("bots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wizard_done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gateway_running_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_pairing_approved_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("first_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column("last_step", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "status IN ('in_progress', 'success', 'failed', 'expired')",
            name="ck_onboarding_run_status",
        ),
    )
    op.create_index(
        "ix_onboarding_run_user_status",
        "onboarding_run",
        ["user_id", "status"],
    )

    # 3. bots: add Phase 4 state-cache columns (GATEWAY-01).
    with op.batch_alter_table("bots") as batch:
        batch.add_column(
            sa.Column(
                "gateway_state_cache",
                sa.String(length=16),
                nullable=False,
                server_default="unconfigured",
            )
        )
        batch.add_column(
            sa.Column(
                "gateway_state_changed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("bots") as batch:
        batch.drop_column("gateway_state_changed_at")
        batch.drop_column("gateway_state_cache")

    op.drop_index("ix_onboarding_run_user_status", table_name="onboarding_run")
    op.drop_table("onboarding_run")

    op.drop_index("ix_pairings_dedupe_pending", table_name="pairings")
    op.drop_index("ix_pairings_status_expires", table_name="pairings")
    op.drop_index("ix_pairings_bot_id", table_name="pairings")
    op.drop_table("pairings")
