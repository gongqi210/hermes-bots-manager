from __future__ import annotations

from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_utils import decode_token
from app.db.session import get_session
from app.middleware.context import current_user_id
from app.models.user import User

_bearer = HTTPBearer(auto_error=False)

# In-memory token blacklist (Phase 1 simplification — persistence to M2).
_revoked: set[str] = set()


def revoke_token(token: str) -> None:
    _revoked.add(token)


def is_revoked(token: str) -> bool:
    return token in _revoked


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 Bearer token")
    token = creds.credentials
    if is_revoked(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 已失效")
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token 已过期"
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 无效") from None
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 类型错误")
    uid = int(payload["sub"])
    user = await session.scalar(select(User).where(User.id == uid))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    current_user_id.set(user.id)
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "token": token,
    }
