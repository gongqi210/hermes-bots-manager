"""OnboardingRun ORM model — Phase 4 3-minute KPI 埋点 (CONTEXT D-19).

One row per user attempt to onboard a Bot end-to-end. Timestamps are filled in
as the user progresses through the funnel; ``total_duration_ms`` is computed
explicitly by the tracker when the run reaches a terminal state — never auto.

Status state machine:

    in_progress --(first @-bot reply received)--> success
    in_progress --(any irrecoverable failure)--> failed
    in_progress --(no progress for >3min)--> expired

CheckConstraint on ``status`` mirrors the ``OnboardingStatus`` ``StrEnum`` so
DB rejects garbage values regardless of caller. Composite index on
``(user_id, status)`` supports "is this user already in a live run?" lookups
which the tracker uses on every wizard / pairing event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OnboardingStatus(StrEnum):
    """State machine vocabulary for ``OnboardingRun.status``."""

    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"


class OnboardingRun(Base):
    """3-minute KPI 埋点 — one row per Bot onboarding attempt by a user."""

    __tablename__ = "onboarding_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'success', 'failed', 'expired')",
            name="ck_onboarding_run_status",
        ),
        Index("ix_onboarding_run_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    bot_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bots.id", ondelete="SET NULL"), nullable=True
    )

    # 7 funnel timestamps from CONTEXT D-19 — only ``started_at`` is required.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    wizard_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gateway_running_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_pairing_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Computed by the tracker at terminal state — NEVER auto.
    total_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=OnboardingStatus.IN_PROGRESS.value,
        server_default=OnboardingStatus.IN_PROGRESS.value,
    )
    last_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
