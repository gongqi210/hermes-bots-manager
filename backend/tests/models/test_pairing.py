"""Tests for Pairing ORM model — Phase 4 Plan 02 Task 1.

Behavior tests covered (per plan):
- Test 1: Round-trip a Pairing row via SQLAlchemy session.
- Test 2: status accepts only pending/approved/rejected/expired.
- Test 3: Inserting a Pairing without code_hash raises IntegrityError.
- Test 6: Bot model gains gateway_state_cache + gateway_state_changed_at.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Bot, Pairing, PairingStatus


async def _make_bot(session: AsyncSession, name: str = "test-bot") -> Bot:
    bot = Bot(name=name, tags=[])
    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    return bot


async def test_pairing_roundtrip(session: AsyncSession) -> None:
    """Test 1: Pairing row persists and round-trips through the session."""
    bot = await _make_bot(session)
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=10)
    p = Pairing(
        bot_id=bot.id,
        code_hash="a" * 64,
        code_last4="X9F2",
        expires_at=expires,
        status=PairingStatus.PENDING.value,
        intercepted_at=now,
    )
    session.add(p)
    await session.commit()
    await session.refresh(p)

    fetched = (await session.execute(select(Pairing).where(Pairing.id == p.id))).scalar_one()
    assert fetched.bot_id == bot.id
    assert fetched.code_hash == "a" * 64
    assert fetched.code_last4 == "X9F2"
    assert fetched.status == "pending"
    assert fetched.platform == "feishu"  # server_default applies
    # plaintext stays None unless explicitly set
    assert fetched.code_plaintext is None
    # processed_at NULL until status transitions
    assert fetched.processed_at is None


async def test_pairing_status_accepts_valid_values(session: AsyncSession) -> None:
    """Test 2 (positive): all four valid statuses persist via StrEnum values."""
    bot = await _make_bot(session, name="bot-statuses")
    now = datetime.now(UTC)
    for s in PairingStatus:
        p = Pairing(
            bot_id=bot.id,
            code_hash="b" * 64,
            code_last4="Z" + s.value[:3].upper(),
            intercepted_at=now,
            expires_at=now + timedelta(minutes=10),
            status=s.value,
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)
        assert p.status == s.value


async def test_pairing_status_rejects_invalid_value(session: AsyncSession) -> None:
    """Test 2 (negative): invalid status string raises IntegrityError (CheckConstraint)."""
    bot = await _make_bot(session, name="bot-invalid")
    now = datetime.now(UTC)
    p = Pairing(
        bot_id=bot.id,
        code_hash="c" * 64,
        code_last4="ABCD",
        intercepted_at=now,
        expires_at=now + timedelta(minutes=10),
        status="bogus",
    )
    session.add(p)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_pairing_code_hash_required(session: AsyncSession) -> None:
    """Test 3: missing code_hash raises IntegrityError (NOT NULL)."""
    bot = await _make_bot(session, name="bot-nohash")
    now = datetime.now(UTC)
    p = Pairing(
        bot_id=bot.id,
        code_hash=None,  # type: ignore[arg-type]
        code_last4="WXYZ",
        intercepted_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    session.add(p)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_bot_has_gateway_state_columns(session: AsyncSession) -> None:
    """Test 6: Bot gains gateway_state_cache + gateway_state_changed_at."""
    bot = await _make_bot(session, name="bot-state")
    fetched = (await session.execute(select(Bot).where(Bot.id == bot.id))).scalar_one()
    # gateway_state_cache defaults to "unconfigured"
    assert fetched.gateway_state_cache == "unconfigured"
    assert fetched.gateway_state_changed_at is None
    # And the column descriptors exist on the mapper
    cols = {c.name for c in inspect(Bot).columns}
    assert "gateway_state_cache" in cols
    assert "gateway_state_changed_at" in cols
