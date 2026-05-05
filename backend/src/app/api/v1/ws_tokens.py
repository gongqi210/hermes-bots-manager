"""POST /api/v1/ws-token — short-TTL WS bearer-token issuer (GATEWAY-13).

Issues a 60-second JWT bound to a specific bot via the ``aud`` claim. The
frontend hits this endpoint just before opening the gateway-log WebSocket
and uses the returned ``token`` as the ``?token=`` query parameter on the
WS URL (Pitfall #8 — same-origin internal tool, query-param auth is fine).

RBAC: any authenticated user (Viewer+) may request a WS token. The role
travels in the JWT payload so the WS endpoint can enforce per-action
authorization (e.g. log filter privileges) without an extra DB read.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.profile_fs import ProfileFsAdapter
from app.auth.deps import get_current_user
from app.auth.jwt_utils import encode_ws_token
from app.auth.rbac import Role, require_role
from app.db.session import get_session
from app.models.bot import Bot
from app.schemas.gateway import WSTokenRequest, WSTokenResponse

router = APIRouter(tags=["ws-token"])


async def _bot_or_profile_exists(
    bot_name: str,
    *,
    session: AsyncSession,
    fs: ProfileFsAdapter,
) -> bool:
    bot = (await session.execute(select(Bot).where(Bot.name == bot_name))).scalar_one_or_none()
    if bot is not None:
        return True
    return bot_name in await fs.list_profiles()


@router.post(
    "/ws-token",
    response_model=WSTokenResponse,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def create_ws_token(
    body: WSTokenRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> WSTokenResponse:
    """Mint a 60-second JWT for the gateway-log WS of ``body.bot_name``."""
    fs: ProfileFsAdapter = request.app.state.fs
    if not await _bot_or_profile_exists(body.bot_name, session=session, fs=fs):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot '{body.bot_name}' 不存在",
        )
    token, expires_in = encode_ws_token(
        sub=str(current_user["id"]),
        role=current_user["role"],
        bot_name=body.bot_name,
    )
    return WSTokenResponse(token=token, expires_in=expires_in)
