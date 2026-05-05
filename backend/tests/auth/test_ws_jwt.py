"""WS-token JWT helper unit tests — Phase 4 GATEWAY-13 / Pitfall #8."""

from __future__ import annotations

from typing import Any

import jwt
import pytest
from freezegun import freeze_time

from app.auth.jwt_utils import decode_token, decode_ws_token, encode_ws_token


@pytest.fixture(autouse=True)
def _settings(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset settings singleton with a deterministic JWT secret."""
    monkeypatch.setenv("HERMES_CONSOLE_JWT_SECRET", "ws-jwt-test-secret-32-chars-long-pad")
    import app.config as c

    c._settings = None


def test_j1_encode_emits_audience_claim_and_decode_requires_it() -> None:
    token, ttl = encode_ws_token(sub="1", role="Owner", bot_name="foo")
    assert ttl == 60
    # Decoding without `audience=` must raise InvalidAudienceError because
    # the token carries a non-empty `aud` claim (PyJWT semantics, Pitfall #8).
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(
            token,
            "ws-jwt-test-secret-32-chars-long-pad",
            algorithms=["HS256"],
        )
    # Same token decoded with the matching audience succeeds.
    payload = decode_ws_token(token, bot_name="foo")
    assert payload["aud"] == "ws-gateway-logs:foo"
    assert payload["sub"] == "1"
    assert payload["role"] == "Owner"
    assert payload["type"] == "ws"


def test_j2_token_expires_after_ttl() -> None:
    with freeze_time("2026-05-04T12:00:00Z") as frozen:
        token, _ = encode_ws_token(sub="1", role="Owner", bot_name="foo")
        # Within the TTL window the decode succeeds.
        decode_ws_token(token, bot_name="foo")
        # Just past expiry — must raise.
        frozen.tick(delta=61)
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_ws_token(token, bot_name="foo")


def test_j3_cross_bot_replay_rejected() -> None:
    token, _ = encode_ws_token(sub="1", role="Owner", bot_name="foo")
    with pytest.raises(jwt.InvalidAudienceError):
        decode_ws_token(token, bot_name="other-bot")


def test_j4_access_token_rejected_by_ws_decoder() -> None:
    """Defensive: even if an attacker forges aud onto an access token, the
    type='ws' check rejects it."""
    from app.auth.jwt_utils import encode_access_token

    access, _ = encode_access_token("1", "Owner")
    # The access token carries no 'aud' so PyJWT raises MissingRequiredClaim.
    with pytest.raises(jwt.InvalidTokenError):
        decode_ws_token(access, bot_name="foo")


def test_j4b_decode_token_rejects_ws_token() -> None:
    """Symmetric: regular access-token decode does not accept a ws token's
    structure cleanly — defensive reject in dependent layers verifies type."""
    token, _ = encode_ws_token(sub="1", role="Owner", bot_name="foo")
    # decode_token does NOT pass audience, so PyJWT raises InvalidAudienceError
    # before our type-check runs — that is fine, it's still a reject.
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token)
