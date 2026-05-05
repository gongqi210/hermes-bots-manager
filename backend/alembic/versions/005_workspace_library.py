"""add workspace_library table (Phase 5 WORKSPACE-02)

Revision ID: 005
Revises: 004
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_library",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("path", sa.String(1024), nullable=False, unique=True),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("registered_by", sa.Integer(), nullable=True),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("workspace_library")
