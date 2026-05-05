"""Unit tests for OnboardingTracker — Phase 4 D-19 / 3-min KPI 埋点."""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bot import Bot
from app.models.onboarding_run import OnboardingRun, OnboardingStatus
from app.models.user import User
from app.services.onboarding_tracker import OnboardingTracker


async def _seed_user(session: AsyncSession, username: str = "tester") -> User:
    user = User(username=username, password_hash="x", role="owner")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _seed_bot(session: AsyncSession, name: str = "foo") -> Bot:
    bot = Bot(name=name)
    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    return bot


async def test_ot1_hook_login_creates_new_in_progress_run(session: AsyncSession) -> None:
    """OT1 — first login creates a new in_progress run; second login updates login_at."""
    user = await _seed_user(session)
    run_id = await OnboardingTracker.hook_login(session, user.id)
    await session.commit()

    rows = (await session.execute(select(OnboardingRun))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == run_id
    assert rows[0].status == OnboardingStatus.IN_PROGRESS.value
    assert rows[0].login_at is not None
    # Normalize to naive UTC — SQLite returns naive datetimes after expiry but
    # the in-memory write is aware until we refresh through the session.
    first_login_at = (
        rows[0].login_at.replace(tzinfo=None) if rows[0].login_at.tzinfo else rows[0].login_at
    )

    # Re-login: should update login_at on the existing in_progress run, NOT
    # create a second row.
    await asyncio.sleep(0.005)
    same_run_id = await OnboardingTracker.hook_login(session, user.id)
    await session.commit()
    assert same_run_id == run_id
    rows = (await session.execute(select(OnboardingRun))).scalars().all()
    assert len(rows) == 1
    second_login_at = (
        rows[0].login_at.replace(tzinfo=None) if rows[0].login_at.tzinfo else rows[0].login_at
    )
    assert second_login_at >= first_login_at


async def test_ot2_hook_wizard_done_sets_bot_id_and_step(session: AsyncSession) -> None:
    """OT2 — wizard_done stamps wizard_done_at + binds bot_id + last_step."""
    user = await _seed_user(session)
    bot = await _seed_bot(session)
    await OnboardingTracker.hook_login(session, user.id)
    await session.commit()

    await OnboardingTracker.hook_wizard_done(session, user.id, bot.id)
    await session.commit()

    run = (await session.execute(select(OnboardingRun))).scalar_one()
    assert run.wizard_done_at is not None
    assert run.bot_id == bot.id
    assert run.last_step == "wizard_done"


async def test_ot3_hook_gateway_running_sets_timestamp(session: AsyncSession) -> None:
    """OT3 — gateway_running stamps gateway_running_at + last_step."""
    user = await _seed_user(session)
    await OnboardingTracker.hook_login(session, user.id)
    await session.commit()

    await OnboardingTracker.hook_gateway_running(session, user.id)
    await session.commit()

    run = (await session.execute(select(OnboardingRun))).scalar_one()
    assert run.gateway_running_at is not None
    assert run.last_step == "gateway_running"


async def test_ot4_hook_first_pairing_approved_is_idempotent(session: AsyncSession) -> None:
    """OT4 — first_pairing_approved stamps once; second call is a no-op."""
    user = await _seed_user(session)
    await OnboardingTracker.hook_login(session, user.id)
    await session.commit()

    await OnboardingTracker.hook_first_pairing_approved(session, user.id)
    await session.commit()

    run = (await session.execute(select(OnboardingRun))).scalar_one()
    first_ts = run.first_pairing_approved_at
    assert first_ts is not None

    # Second call must NOT overwrite the timestamp.
    await asyncio.sleep(0.005)
    await OnboardingTracker.hook_first_pairing_approved(session, user.id)
    await session.commit()
    await session.refresh(run)
    assert run.first_pairing_approved_at == first_ts


async def test_ot5_hook_first_message_completes_run(session: AsyncSession) -> None:
    """OT5 — first_message stamps + computes total_duration_ms + flips to success."""
    user = await _seed_user(session)
    await OnboardingTracker.hook_login(session, user.id)
    await session.commit()

    # Sleep a tiny bit so duration is > 0.
    await asyncio.sleep(0.01)
    await OnboardingTracker.hook_first_message(session, user.id)
    await session.commit()

    run = (await session.execute(select(OnboardingRun))).scalar_one()
    assert run.first_message_at is not None
    assert run.total_duration_ms is not None
    assert run.total_duration_ms >= 0
    assert run.status == OnboardingStatus.SUCCESS.value
    assert run.last_step == "first_message"


async def test_hook_first_message_with_explicit_run_id(session: AsyncSession) -> None:
    """hook_first_message accepts run_id to amend a specific run (D-19 fallback)."""
    user = await _seed_user(session)
    run_id = await OnboardingTracker.hook_login(session, user.id)
    await session.commit()

    await OnboardingTracker.hook_first_message(session, user.id, run_id=run_id)
    await session.commit()

    run = (
        await session.execute(select(OnboardingRun).where(OnboardingRun.id == run_id))
    ).scalar_one()
    assert run.status == OnboardingStatus.SUCCESS.value


async def test_hooks_silently_no_op_when_no_in_progress_run(session: AsyncSession) -> None:
    """All non-login hooks return None and do not raise when no in_progress run exists."""
    user = await _seed_user(session)
    bot = await _seed_bot(session)
    await OnboardingTracker.hook_wizard_done(session, user.id, bot.id)
    await OnboardingTracker.hook_gateway_running(session, user.id)
    await OnboardingTracker.hook_first_pairing_approved(session, user.id)
    await OnboardingTracker.hook_first_message(session, user.id)
    rows = (await session.execute(select(OnboardingRun))).scalars().all()
    assert rows == []
