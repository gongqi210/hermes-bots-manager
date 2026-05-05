"""Pydantic schemas for the Phase 4 onboarding-KPI tracker (CONTEXT D-19)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class OnboardingRunOut(BaseModel):
    """Wire format for a single :class:`app.models.onboarding_run.OnboardingRun`.

    Mirrors all 7 funnel timestamps so the dashboard can render the
    "3-minute KPI" Sankey-style breakdown without server-side joins.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    bot_id: int | None
    started_at: datetime
    login_at: datetime | None
    wizard_done_at: datetime | None
    gateway_running_at: datetime | None
    first_pairing_approved_at: datetime | None
    first_message_at: datetime | None
    total_duration_ms: int | None
    status: Literal["in_progress", "success", "failed", "expired"]
    last_step: str | None


class MarkFirstMessageIn(BaseModel):
    """D-19 fallback: operator manually confirms the first @-bot reply landed.

    Used when the auto-detection path (gateway log line) does not fire — for
    example when a Bot is paired but no one has @-mentioned it within the
    3-minute window. The dashboard exposes a "我已收到回复" button that posts
    this body.
    """

    run_id: int
