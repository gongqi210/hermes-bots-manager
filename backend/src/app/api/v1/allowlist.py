"""Allowlist REST router — Phase 4 GATEWAY-15 / GATEWAY-16.

* ``GET  /api/v1/bots/{bot_name}/allowlist`` — Viewer+; returns the current
  ``FEISHU_ALLOWED_USERS`` from the .env (FINDING-04: comma-separated).
* ``PUT  /api/v1/bots/{bot_name}/allowlist`` — Editor+; rewrites the
  allowlist via :meth:`ProfileFsAdapter.write_allowed_users` which
  preserves every other key in the file.

The adapter performs deduplication + whitespace trim + separator/newline
rejection; the router only translates ``ValueError`` to HTTP 422.
"""

from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.profile_fs import ProfileFsAdapter
from app.auth.rbac import Role, require_role
from app.db.session import get_session
from app.models.bot import Bot
from app.models.user import User
from app.schemas.gateway import AllowlistOut, AllowlistUpdateIn
from app.schemas.management import AllowlistPresetsOut, AllowlistPresetUpdateIn

router = APIRouter(tags=["allowlist"])


async def _ensure_known_profile(
    bot_name: str,
    *,
    session: AsyncSession,
    fs: ProfileFsAdapter,
) -> None:
    bot = (await session.execute(select(Bot).where(Bot.name == bot_name))).scalar_one_or_none()
    if bot is not None:
        return
    if bot_name in await fs.list_profiles():
        return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")


@router.get(
    "/bots/{bot_name}/allowlist",
    response_model=AllowlistOut,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def get_allowlist(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AllowlistOut:
    fs: ProfileFsAdapter = request.app.state.fs
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    users = await fs.read_allowed_users(bot_name)
    return AllowlistOut(bot_name=bot_name, users=users)


@router.put(
    "/bots/{bot_name}/allowlist",
    response_model=AllowlistOut,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def put_allowlist(
    bot_name: str,
    body: AllowlistUpdateIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AllowlistOut:
    fs: ProfileFsAdapter = request.app.state.fs
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    try:
        await fs.write_allowed_users(bot_name, body.users)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    users = await fs.read_allowed_users(bot_name)
    return AllowlistOut(bot_name=bot_name, users=users)


# ---------------------------------------------------------------------------
# Phase 5 plan 05-05 — allowlist presets (open / owner_admin / custom)
# ---------------------------------------------------------------------------


async def _resolve_owner_admin_ids(session: AsyncSession) -> tuple[list[str], str | None]:
    """Return (resolved_feishu_ids, warning_message)."""
    rows = (
        (
            await session.execute(
                select(User).where(User.role.in_(["Owner", "Admin"]))
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return [], "尚未创建任何 Owner/Admin 账户, 无法应用此预设"
    ids = [r.feishu_user_id for r in rows if r.feishu_user_id]
    if not ids:
        return [], (
            "Owner/Admin 账户尚未绑定飞书 OpenID, 请先在用户管理中填入 feishu_user_id"
        )
    # Stable ordering for deterministic equality checks.
    return sorted(set(ids)), None


@router.get(
    "/bots/{bot_name}/allowlist/presets",
    response_model=AllowlistPresetsOut,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def get_allowlist_presets(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AllowlistPresetsOut:
    fs: ProfileFsAdapter = request.app.state.fs
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    current = await fs.read_allowed_users(bot_name)
    owner_admin_ids, warning = await _resolve_owner_admin_ids(session)
    return AllowlistPresetsOut(
        bot_name=bot_name,
        open=[],
        owner_admin=owner_admin_ids,
        custom=current,
        owner_admin_warning=warning,
    )


@router.put(
    "/bots/{bot_name}/allowlist/preset",
    response_model=AllowlistOut,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def put_allowlist_preset(
    bot_name: str,
    body: AllowlistPresetUpdateIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AllowlistOut:
    fs: ProfileFsAdapter = request.app.state.fs
    await _ensure_known_profile(bot_name, session=session, fs=fs)
    with suppress(Exception):
        await fs.snapshot_profile(bot_name)

    if body.preset == "open":
        next_users: list[str] = []
    elif body.preset == "owner_admin":
        owner_admin_ids, warning = await _resolve_owner_admin_ids(session)
        if not owner_admin_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=warning or "无法解析 Owner/Admin 飞书 OpenID",
            )
        next_users = owner_admin_ids
    else:  # custom — no-op, return current list unchanged
        users = await fs.read_allowed_users(bot_name)
        return AllowlistOut(bot_name=bot_name, users=users)

    try:
        await fs.write_allowed_users(bot_name, next_users)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    users = await fs.read_allowed_users(bot_name)
    return AllowlistOut(bot_name=bot_name, users=users)
