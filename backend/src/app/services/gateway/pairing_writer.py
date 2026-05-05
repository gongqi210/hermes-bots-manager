"""Pairing persistence layer — Phase 4 GATEWAY-10 / GATEWAY-12.

The :class:`GatewaySupervisor` runs OUTSIDE any HTTP request context, so it
cannot reuse the request-scoped :func:`app.db.session.get_session` dependency
— each pairing write opens its own session via the application's
``async_sessionmaker``.

Storage discipline (CONTEXT D-04 / D-12):

* On intercept we write the plaintext code into ``code_plaintext`` so the
  REST approve endpoint can shell out to ``hermes pairing approve feishu
  <code>`` without forcing the operator to retype it from the UI. We also
  write ``code_hash`` (sha256 hex) and ``code_last4`` for audit / display.
* The expires_at TTL is exactly ``intercepted_at + 10min`` per GATEWAY-12.
* :func:`expire_old_pairings` runs every 60s from the FastAPI lifespan —
  it sets ``status='expired'``, clears ``code_plaintext``, and stamps
  ``processed_at`` so the row becomes archival.

Dedupe (MAJOR 8):

* SELECT-before-INSERT is the friendly path — quietly skips a duplicate
  intercept and avoids ``IntegrityError`` log noise in the common case.
* The partial unique index ``ix_pairings_dedupe_pending(bot_id, code_hash)
  WHERE status='pending'`` (added in alembic 004) is the correctness backstop
  on theoretical race. We catch ``IntegrityError`` and treat it as dedupe.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.bot import Bot
from app.models.pairing import Pairing, PairingStatus
from app.services.gateway.pairing_extractor import PairingCandidate

logger = logging.getLogger(__name__)

# GATEWAY-12: pairing TTL is 10 minutes from intercept.
PAIRING_TTL_MINUTES = 10


def make_pairing_writer(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> Callable[[PairingCandidate], Awaitable[None]]:
    """Build a write closure bound to ``sessionmaker``.

    The Supervisor stores the returned coroutine factory and awaits it on
    every intercepted candidate — keeping the sessionmaker out of Supervisor
    constructor signatures keeps the unit tests fully synchronous and offline.
    """

    async def write_pairing(candidate: PairingCandidate) -> None:
        async with sessionmaker() as session:
            bot_row = (
                await session.execute(select(Bot).where(Bot.name == candidate.bot_name))
            ).scalar_one_or_none()
            if bot_row is None:
                # Pairing observed for a profile we have no DB row for —
                # WARN and drop. This can happen in the brief window between
                # ``hermes profile create`` finishing and the wizard inserting
                # the Bot row; the next intercept on the retry will succeed.
                logger.warning(
                    "pairing intercepted for unknown bot %s; dropping", candidate.bot_name
                )
                return

            code_hash = hashlib.sha256(candidate.code.encode("utf-8")).hexdigest()

            # Friendly skip-on-duplicate (avoids IntegrityError log noise).
            existing = (
                await session.execute(
                    select(Pairing).where(
                        Pairing.bot_id == bot_row.id,
                        Pairing.code_hash == code_hash,
                        Pairing.status == PairingStatus.PENDING.value,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                logger.debug(
                    "pairing dedupe (friendly skip) for bot=%s last4=%s",
                    candidate.bot_name,
                    candidate.code[-4:],
                )
                return

            now = datetime.now(UTC)
            row = Pairing(
                bot_id=bot_row.id,
                platform=candidate.platform,
                code_plaintext=candidate.code,
                code_hash=code_hash,
                code_last4=candidate.code[-4:],
                feishu_user_id=candidate.feishu_user_id,
                status=PairingStatus.PENDING.value,
                intercepted_at=now,
                expires_at=now + timedelta(minutes=PAIRING_TTL_MINUTES),
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # MAJOR 8: theoretical race between SELECT and INSERT — the
                # partial unique index rejects the duplicate. Treat as dedupe.
                await session.rollback()
                logger.debug(
                    "pairing dedupe (IntegrityError backstop) for bot=%s last4=%s",
                    candidate.bot_name,
                    candidate.code[-4:],
                )

    return write_pairing


async def expire_old_pairings(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """Move pending pairings past ``expires_at`` to ``status='expired'``.

    GATEWAY-12 contract: only the hash + last4 survive after expiry —
    ``code_plaintext`` is cleared. ``processed_at`` is stamped so the row
    counts as a terminal state for audit purposes.

    Returns the number of rows transitioned. Lifespan TTL loop calls this
    every 60s; logging is kept to a single info line per batch with non-zero
    work so the steady-state log is silent.
    """
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        result = await session.execute(
            select(Pairing).where(
                Pairing.status == PairingStatus.PENDING.value,
                Pairing.expires_at <= now,
            )
        )
        rows = list(result.scalars().all())
        if not rows:
            return 0
        for row in rows:
            row.status = PairingStatus.EXPIRED.value
            row.code_plaintext = None
            row.processed_at = now
        await session.commit()
        logger.info("pairing TTL expired %d row(s)", len(rows))
        return len(rows)
