"""Tests for OnboardingRun ORM model — Phase 4 Plan 02 Task 1.

Behavior tests covered (per plan):
- Test 4: OnboardingRun with started_at + nullable timestamps round-trips;
  total_duration_ms is set explicitly (no auto-compute).
- Test 5: status accepts only in_progress/success/failed/expired.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OnboardingRun, OnboardingStatus, User


async def _make_user(session: AsyncSession, username: str = "kpi-user") -> User:
    u = User(username=username, password_hash="x", role="Owner")
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


async def test_onboarding_run_roundtrip(session: AsyncSession) -> None:
    """Test 4: started_at persists, optional timestamps stay NULL."""
    user = await _make_user(session)
    started = datetime.now(UTC)
    run = OnboardingRun(user_id=user.id, started_at=started)
    session.add(run)
    await session.commit()
    await session.refresh(run)

    fetched = (
        await session.execute(select(OnboardingRun).where(OnboardingRun.id == run.id))
    ).scalar_one()
    assert fetched.user_id == user.id
    assert fetched.bot_id is None
    assert fetched.login_at is None
    assert fetched.wizard_done_at is None
    assert fetched.gateway_running_at is None
    assert fetched.first_pairing_approved_at is None
    assert fetched.first_message_at is None
    assert fetched.total_duration_ms is None  # not auto-computed
    assert fetched.status == "in_progress"
    assert fetched.last_step is None


async def test_onboarding_run_total_duration_when_set(session: AsyncSession) -> None:
    """Test 4 (continued): total_duration_ms only stored when explicitly set."""
    user = await _make_user(session, username="kpi-set")
    started = datetime.now(UTC)
    run = OnboardingRun(
        user_id=user.id,
        started_at=started,
        login_at=started + timedelta(seconds=5),
        wizard_done_at=started + timedelta(seconds=60),
        gateway_running_at=started + timedelta(seconds=90),
        first_pairing_approved_at=started + timedelta(seconds=120),
        first_message_at=started + timedelta(seconds=170),
        total_duration_ms=170_000,
        status=OnboardingStatus.SUCCESS.value,
        last_step="first_message",
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    assert run.total_duration_ms == 170_000
    assert run.status == "success"
    assert run.last_step == "first_message"


async def test_onboarding_run_status_accepts_valid_values(session: AsyncSession) -> None:
    """Test 5 (positive): every OnboardingStatus value persists."""
    user = await _make_user(session, username="kpi-statuses")
    started = datetime.now(UTC)
    for s in OnboardingStatus:
        run = OnboardingRun(user_id=user.id, started_at=started, status=s.value)
        session.add(run)
        await session.commit()
        await session.refresh(run)
        assert run.status == s.value


async def test_onboarding_run_status_rejects_invalid_value(session: AsyncSession) -> None:
    """Test 5 (negative): invalid status string raises IntegrityError."""
    user = await _make_user(session, username="kpi-invalid")
    started = datetime.now(UTC)
    run = OnboardingRun(user_id=user.id, started_at=started, status="bogus")
    session.add(run)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
