from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from app.config import get_settings

TokenType = Literal["access", "refresh"]


def _encode(sub: str, role: str, token_type: TokenType, ttl_seconds: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": sub,
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def encode_access_token(sub: str, role: str) -> tuple[str, int]:
    s = get_settings()
    return _encode(sub, role, "access", s.jwt_access_ttl_seconds), s.jwt_access_ttl_seconds


def encode_refresh_token(sub: str, role: str) -> tuple[str, int]:
    s = get_settings()
    return _encode(sub, role, "refresh", s.jwt_refresh_ttl_seconds), s.jwt_refresh_ttl_seconds


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises jwt.InvalidTokenError on failure."""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )


# ----------------------------------------------------------------------
# Phase 4 — short-TTL WebSocket tokens (gateway log stream).
#
# A separate token family with an explicit ``aud`` claim per Pitfall #8 of
# 04-RESEARCH.md. The audience binds the token to a specific bot, so a
# leaked-from-bot-A token cannot be replayed against the WS endpoint of
# bot-B. ``type='ws'`` discriminator defends against access/refresh tokens
# (which lack ``aud``) being accepted on the WS handshake.
# ----------------------------------------------------------------------


WS_TOKEN_TTL_SECONDS = 60  # GATEWAY-08


def _ws_audience(bot_name: str) -> str:
    return f"ws-gateway-logs:{bot_name}"


def encode_ws_token(
    sub: str, role: str, *, bot_name: str, ttl_seconds: int = WS_TOKEN_TTL_SECONDS
) -> tuple[str, int]:
    """Issue a 60s WS token bound to ``bot_name`` via the ``aud`` claim.

    Pitfall #8: PyJWT only verifies ``aud`` when ``audience=...`` is passed
    on decode. The encoder emits the audience unconditionally; downstream
    decoders that omit ``audience=...`` will (correctly) reject the token
    with :class:`jwt.InvalidAudienceError`.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": sub,
        "role": role,
        "type": "ws",
        "aud": _ws_audience(bot_name),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, ttl_seconds


def decode_ws_token(token: str, *, bot_name: str) -> dict[str, Any]:
    """Decode + verify a WS token, requiring ``aud`` to match ``bot_name``.

    Raises :class:`jwt.InvalidTokenError` (or one of its subclasses such as
    ``ExpiredSignatureError`` / ``InvalidAudienceError``) on failure. Also
    rejects access/refresh tokens that happen to carry a matching audience
    via the ``type='ws'`` defensive check.
    """
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        audience=_ws_audience(bot_name),
    )
    if payload.get("type") != "ws":
        raise jwt.InvalidTokenError("not a ws token")
    return payload
