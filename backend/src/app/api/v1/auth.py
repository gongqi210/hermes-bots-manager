from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, is_revoked, revoke_token
from app.auth.jwt_utils import decode_token, encode_access_token, encode_refresh_token
from app.auth.password import hash_password, validate_password_policy, verify_password
from app.auth.rbac import Role, require_role
from app.db.session import get_session
from app.middleware.context import current_user_id
from app.models.user import User
from app.schemas.auth import (
    AccessTokenOut,
    BootstrapRequest,
    CreateUserRequest,
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    TokenPair,
    UserOut,
)
from app.services.onboarding_tracker import OnboardingTracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _build_tokens(user: User) -> TokenPair:
    access, a_ttl = encode_access_token(str(user.id), user.role)
    refresh, r_ttl = encode_refresh_token(str(user.id), user.role)
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        access_expires_in=a_ttl,
        refresh_expires_in=r_ttl,
    )


@router.post(
    "/bootstrap",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bootstrap_owner(
    body: BootstrapRequest, session: AsyncSession = Depends(get_session)
) -> LoginResponse:
    count = await session.scalar(select(func.count(User.id)))
    if count and count > 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已完成首次初始化")
    try:
        validate_password_policy(body.password.get_secret_value())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None
    user = User(
        username=body.username,
        password_hash=hash_password(body.password.get_secret_value()),
        role=Role.OWNER.value,
    )
    session.add(user)
    await session.flush()
    # Record actor for audit middleware.
    current_user_id.set(user.id)
    tokens = _build_tokens(user)
    return LoginResponse(user=UserOut.model_validate(user), tokens=tokens)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> LoginResponse:
    user = await session.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password.get_secret_value(), user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    current_user_id.set(user.id)
    # D-19 / 3-min KPI 埋点 — log-in is the funnel entry. Wrapped in try to
    # keep the auth path resilient: a failed onboarding write must NEVER
    # block the user from logging in.
    try:
        await OnboardingTracker.hook_login(session, user.id)
    except Exception:
        logger.warning("OnboardingTracker.hook_login failed for user %s", user.id, exc_info=True)
    return LoginResponse(user=UserOut.model_validate(user), tokens=_build_tokens(user))


@router.get("/me", response_model=UserOut)
async def me(
    current: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    user = await session.scalar(select(User).where(User.id == current["id"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(current: dict[str, Any] = Depends(get_current_user)) -> dict[str, bool]:
    revoke_token(current["token"])
    return {"ok": True}


@router.post("/refresh", response_model=AccessTokenOut)
async def refresh(body: RefreshRequest) -> AccessTokenOut:
    if is_revoked(body.refresh_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token 已失效")
    try:
        payload = decode_token(body.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token 已过期"
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token 无效"
        ) from None
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token 类型错误")
    token, ttl = encode_access_token(payload["sub"], payload["role"])
    return AccessTokenOut(access_token=token, access_expires_in=ttl)


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OWNER))],
)
async def create_user(
    body: CreateUserRequest, session: AsyncSession = Depends(get_session)
) -> UserOut:
    try:
        validate_password_policy(body.password.get_secret_value())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None
    existing = await session.scalar(select(User).where(User.username == body.username))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password.get_secret_value()),
        role=body.role,
        created_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return UserOut.model_validate(user)
