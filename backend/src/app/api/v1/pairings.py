"""Pairing REST router — Phase 4 GATEWAY-11 / GATEWAY-12.

Endpoints (Owner / Admin only — D-12: pairing approval is a privileged
operation that grants a flesh-and-blood human access to the bot).

* ``GET    /api/v1/pairings``                   — list pending; ``?bot_name=`` filter.
* ``POST   /api/v1/pairings/{id}/approve``      — call hermes pairing approve.
* ``POST   /api/v1/pairings/{id}/reject``       — DB-only (Hermes v0.8 has
  no ``reject`` subcommand per FINDING-05; D-12 allows the operator to
  archive a pending row without touching Hermes).

Wire-format invariant (NFR-02 + GATEWAY-12): ``code_plaintext`` is cleared
to NULL on EVERY transition out of ``pending``. The literal assignment
``pairing.code_plaintext = None`` appears at least 3 times in this module
(approve happy path, reject, expire-on-fly) so the grep contract holds.

FINDING-02 active-profile handling
----------------------------------

Real Hermes usage verified that ``hermes -p <bot> pairing approve ...`` is
profile-scoped even when another profile is marked active globally. The REST
layer therefore relies on the explicit ``-p`` flag instead of blocking approval
on ``active_profile``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.hermes_cli import HermesCliAdapter, HermesCliError
from app.auth.deps import get_current_user
from app.auth.rbac import Role, require_role
from app.db.session import get_session
from app.models.bot import Bot
from app.models.pairing import Pairing, PairingStatus
from app.schemas.pairing import PairingActionResponse, PairingOut
from app.services.onboarding_tracker import OnboardingTracker

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pairings"])

_PAIRING_TTL_MINUTES = 10
_FEISHU_USER_ID_RE = re.compile(r"\b(ou_[A-Za-z0-9]{20,})\b")
_AGE_RE = re.compile(r"\b(\d+)\s*([smhd])\s+ago\b", re.IGNORECASE)


def _extract_feishu_user_id(raw: str | None) -> str | None:
    if not raw:
        return None
    m = _FEISHU_USER_ID_RE.search(raw)
    return m.group(1) if m else None


def _parse_age(raw: str | None) -> timedelta:
    """Parse Hermes' compact age text, e.g. ``2m ago``. Unknown => zero."""
    if not raw:
        return timedelta(0)
    m = _AGE_RE.search(raw)
    if not m:
        return timedelta(0)
    value = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "s":
        return timedelta(seconds=value)
    if unit == "m":
        return timedelta(minutes=value)
    if unit == "h":
        return timedelta(hours=value)
    return timedelta(days=value)


async def _sync_pending_pairings_from_hermes(
    *,
    session: AsyncSession,
    cli: HermesCliAdapter,
    bot_name: str | None,
) -> None:
    """Best-effort pull of live Hermes pending pairings into the console DB.

    The gateway log interceptor is still useful, but real Hermes profile-scoped
    gateways can write to per-profile logs. Pulling ``pairing list`` on the
    approval-center read path makes the MVP resilient even when the log tailer
    misses a line.
    """
    bot_query = select(Bot)
    if bot_name is not None:
        bot_query = bot_query.where(Bot.name == bot_name)
    bots = list((await session.execute(bot_query)).scalars().all())
    now = datetime.now(UTC)

    for bot in bots:
        try:
            parsed = await cli.pairing_list(bot.name)
        except HermesCliError:
            logger.warning("pairing list sync failed for bot=%s", bot.name, exc_info=True)
            continue
        for pending in parsed.pending:
            if not pending.code or not pending.code.isalnum():
                continue
            age = _parse_age(pending.created)
            if age >= timedelta(minutes=_PAIRING_TTL_MINUTES):
                continue
            code_hash = hashlib.sha256(pending.code.encode("utf-8")).hexdigest()
            existing = (
                await session.execute(
                    select(Pairing).where(
                        Pairing.bot_id == bot.id,
                        Pairing.code_hash == code_hash,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            observed_at = now - age
            session.add(
                Pairing(
                    bot_id=bot.id,
                    platform=pending.platform or "feishu",
                    code_plaintext=pending.code,
                    code_hash=code_hash,
                    code_last4=pending.code[-4:],
                    feishu_user_id=_extract_feishu_user_id(pending.created),
                    status=PairingStatus.PENDING.value,
                    intercepted_at=observed_at,
                    expires_at=observed_at + timedelta(minutes=_PAIRING_TTL_MINUTES),
                )
            )
    await session.commit()


@router.get(
    "/pairings",
    response_model=list[PairingOut],
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def list_pairings(
    request: Request,
    bot_name: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[PairingOut]:
    """List pending pairings (newest-first), optionally filtered by bot."""
    await _sync_pending_pairings_from_hermes(
        session=session,
        cli=request.app.state.cli,
        bot_name=bot_name,
    )
    now = datetime.now(UTC)
    q = (
        select(Pairing, Bot.name)
        .join(Bot, Pairing.bot_id == Bot.id)
        .where(
            Pairing.status == PairingStatus.PENDING.value,
            Pairing.expires_at > now,
        )
    )
    if bot_name is not None:
        q = q.where(Bot.name == bot_name)
    q = q.order_by(Pairing.intercepted_at.desc())
    rows = (await session.execute(q)).all()
    out: list[PairingOut] = []
    for pairing, bn in rows:
        item = PairingOut.model_validate(pairing)
        item.bot_name = bn
        # SQLite returns naive datetimes — coerce to UTC-aware before subtraction.
        expires = pairing.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        item.seconds_to_expiry = max(0, int((expires - now).total_seconds()))
        out.append(item)
    return out


@router.post(
    "/pairings/{pairing_id}/approve",
    response_model=PairingActionResponse,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def approve_pairing(
    pairing_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> PairingActionResponse:
    cli: HermesCliAdapter = request.app.state.cli
    pairing = (
        await session.execute(select(Pairing).where(Pairing.id == pairing_id))
    ).scalar_one_or_none()
    if pairing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pairing not found")
    if pairing.status != PairingStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"pairing 已是 {pairing.status} 状态",
        )
    now = datetime.now(UTC)
    expires_at = pairing.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        # Expire-on-fly transition (defensive — the TTL loop also handles this).
        pairing.status = PairingStatus.EXPIRED.value
        pairing.code_plaintext = None  # GATEWAY-12 — never on the wire
        pairing.processed_at = now
        await session.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="pairing 已过期")
    bot = (await session.execute(select(Bot).where(Bot.id == pairing.bot_id))).scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
    if pairing.code_plaintext is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="pairing 原文已清空, 无法批准(请重新触发配对)",
        )

    try:
        await cli.pairing_approve(bot.name, pairing.code_plaintext)
    except HermesCliError as e:
        if e.hint == "pairing_expired":
            pairing.status = PairingStatus.EXPIRED.value
            pairing.code_plaintext = None  # GATEWAY-12
            pairing.processed_at = datetime.now(UTC)
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_410_GONE, detail="Hermes 端 pairing 已过期"
            ) from e
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"approve 失败: {e.hint} — {str(e)[:200]}",
        ) from e

    pairing.status = PairingStatus.APPROVED.value
    pairing.code_plaintext = None  # GATEWAY-12 — never on the wire
    pairing.processed_at = datetime.now(UTC)
    pairing.processed_by_user_id = current_user["id"]
    try:
        await OnboardingTracker.hook_first_pairing_approved(session, current_user["id"])
    except Exception:
        logger.warning("OnboardingTracker.hook_first_pairing_approved failed", exc_info=True)
    await session.commit()
    return PairingActionResponse(
        id=pairing.id,
        status="approved",
        message="已批准, 请在飞书群 @ 机器人测试",
    )


@router.post(
    "/pairings/{pairing_id}/reject",
    response_model=PairingActionResponse,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def reject_pairing(
    pairing_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> PairingActionResponse:
    """D-12: reject is DB-only — Hermes v0.8 has no ``pairing reject`` subcommand
    (FINDING-05). The row transitions to ``status='rejected'`` so the operator
    can archive false-positive intercepts; the next bona-fide pairing emits a
    fresh code that has to be approved separately.
    """
    pairing = (
        await session.execute(select(Pairing).where(Pairing.id == pairing_id))
    ).scalar_one_or_none()
    if pairing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pairing not found")
    if pairing.status != PairingStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"pairing 已是 {pairing.status} 状态",
        )
    pairing.status = PairingStatus.REJECTED.value
    pairing.code_plaintext = None  # GATEWAY-12 — never on the wire
    pairing.processed_at = datetime.now(UTC)
    pairing.processed_by_user_id = current_user["id"]
    await session.commit()
    return PairingActionResponse(
        id=pairing.id,
        status="rejected",
        message="已拒绝该次配对",
    )
