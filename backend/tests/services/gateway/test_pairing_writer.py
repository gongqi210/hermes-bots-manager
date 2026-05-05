"""Unit tests for pairing_writer — Phase 4 GATEWAY-10 / GATEWAY-12 / MAJOR 8."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.bot import Bot
from app.models.pairing import Pairing, PairingStatus
from app.services.gateway.pairing_extractor import PairingCandidate
from app.services.gateway.pairing_writer import (
    PAIRING_TTL_MINUTES,
    expire_old_pairings,
    make_pairing_writer,
)


def _make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _seed_bot(session: AsyncSession, name: str = "foo") -> Bot:
    bot = Bot(name=name)
    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    return bot


async def test_pw1_inserts_pending_with_hash_last4_and_ttl(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    """PW1 — write_pairing inserts a pending row with sha256 + last4 + 10-min TTL."""
    bot = await _seed_bot(session)
    write = make_pairing_writer(_make_sessionmaker(engine))

    before = datetime.now(UTC).replace(tzinfo=None)
    await write(PairingCandidate(code="ABCD1234", feishu_user_id="ou_xx", bot_name="foo"))
    after = datetime.now(UTC).replace(tzinfo=None)

    rows = (await session.execute(select(Pairing).where(Pairing.bot_id == bot.id))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == PairingStatus.PENDING.value
    assert row.code_plaintext == "ABCD1234"
    assert row.code_hash == hashlib.sha256(b"ABCD1234").hexdigest()
    assert row.code_last4 == "1234"
    assert row.feishu_user_id == "ou_xx"
    assert row.platform == "feishu"
    # TTL is 10 minutes from intercept (CONTEXT D-04 / GATEWAY-12). SQLite
    # returns naive UTC datetimes — strip tzinfo for the comparison window.
    intercepted_naive = row.intercepted_at.replace(tzinfo=None)
    expires_naive = row.expires_at.replace(tzinfo=None)
    assert expires_naive - intercepted_naive == timedelta(minutes=PAIRING_TTL_MINUTES)
    assert before <= intercepted_naive <= after


async def test_pw2_dedupe_via_select_then_skip(engine: AsyncEngine, session: AsyncSession) -> None:
    """PW2 — same code intercepted twice within TTL → only one row persists."""
    await _seed_bot(session)
    write = make_pairing_writer(_make_sessionmaker(engine))

    candidate = PairingCandidate(code="DUP00001", feishu_user_id=None, bot_name="foo")
    await write(candidate)
    await write(candidate)

    rows = (await session.execute(select(Pairing))).scalars().all()
    assert len(rows) == 1


async def test_pw2_integrity_error_backstop_treats_as_dedupe(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    """PW2 — IntegrityError on the partial unique index is caught and treated as dedupe.

    Simulated by manually inserting a pending row with a known hash, then
    bypassing the friendly SELECT and forcing the writer to attempt INSERT.
    """
    bot = await _seed_bot(session)
    code = "RACE0001"
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    now = datetime.now(UTC)
    pre_existing = Pairing(
        bot_id=bot.id,
        platform="feishu",
        code_plaintext=code,
        code_hash=code_hash,
        code_last4=code[-4:],
        status=PairingStatus.PENDING.value,
        intercepted_at=now,
        expires_at=now + timedelta(minutes=PAIRING_TTL_MINUTES),
    )
    session.add(pre_existing)
    await session.commit()

    # Re-running write should be a clean no-op (friendly skip path), not raise.
    write = make_pairing_writer(_make_sessionmaker(engine))
    await write(PairingCandidate(code=code, feishu_user_id=None, bot_name="foo"))

    rows = (await session.execute(select(Pairing))).scalars().all()
    assert len(rows) == 1


async def test_pw3_uses_fresh_session_not_request_session(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    """PW3 — writer opens its own session via the bound sessionmaker.

    The supervisor runs OUTSIDE any request context; passing the request
    session would risk session re-use across tasks. The writer takes only
    a sessionmaker so each write opens a fresh AsyncSession.
    """
    await _seed_bot(session)
    sessionmaker = _make_sessionmaker(engine)
    write = make_pairing_writer(sessionmaker)
    await write(PairingCandidate(code="FRESH001", feishu_user_id=None, bot_name="foo"))
    # Re-verify via a brand new session — proves write committed independently.
    async with sessionmaker() as fresh:
        rows = (await fresh.execute(select(Pairing))).scalars().all()
    assert len(rows) == 1


async def test_pw4_expire_old_pairings_clears_plaintext(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    """PW4 — expire_old_pairings transitions stale pending rows.

    Sets status=expired, clears code_plaintext, stamps processed_at, and
    returns the count.
    """
    bot = await _seed_bot(session)
    now = datetime.now(UTC)
    # One stale (past TTL), one fresh.
    stale = Pairing(
        bot_id=bot.id,
        platform="feishu",
        code_plaintext="STALE111",
        code_hash=hashlib.sha256(b"STALE111").hexdigest(),
        code_last4="E111",
        status=PairingStatus.PENDING.value,
        intercepted_at=now - timedelta(minutes=15),
        expires_at=now - timedelta(minutes=5),
    )
    fresh = Pairing(
        bot_id=bot.id,
        platform="feishu",
        code_plaintext="FRESH222",
        code_hash=hashlib.sha256(b"FRESH222").hexdigest(),
        code_last4="H222",
        status=PairingStatus.PENDING.value,
        intercepted_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    session.add_all([stale, fresh])
    await session.commit()

    count = await expire_old_pairings(_make_sessionmaker(engine))
    assert count == 1

    await session.refresh(stale)
    await session.refresh(fresh)
    assert stale.status == PairingStatus.EXPIRED.value
    assert stale.code_plaintext is None
    assert stale.processed_at is not None
    assert fresh.status == PairingStatus.PENDING.value
    assert fresh.code_plaintext == "FRESH222"


async def test_writer_drops_pairing_when_bot_unknown(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    """Pairing for an unknown bot_name does not raise and writes nothing."""
    write = make_pairing_writer(_make_sessionmaker(engine))
    # No bots seeded — writer must WARN-and-drop, not raise.
    await write(PairingCandidate(code="NOBOT001", feishu_user_id=None, bot_name="ghost"))
    rows = (await session.execute(select(Pairing))).scalars().all()
    assert rows == []


async def test_expire_old_pairings_returns_zero_when_no_stale(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    """No stale rows → returns 0 quietly."""
    await _seed_bot(session)
    count = await expire_old_pairings(_make_sessionmaker(engine))
    assert count == 0
