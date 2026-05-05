from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from app.auth.password import hash_password, validate_password_policy, verify_password
from app.auth.rbac import Role, require_role, role_rank


def test_password_hash_and_verify() -> None:
    h = hash_password("hello12345")
    assert h.startswith("$2b$") or h.startswith("$2a$")  # bcrypt
    assert verify_password("hello12345", h)
    assert not verify_password("wrong12345", h)


def test_password_policy_length() -> None:
    with pytest.raises(ValueError, match="8 位"):
        validate_password_policy("short1")


def test_password_policy_no_letter() -> None:
    with pytest.raises(ValueError, match="字母"):
        validate_password_policy("12345678")


def test_password_policy_no_digit() -> None:
    with pytest.raises(ValueError, match="数字"):
        validate_password_policy("abcdefgh")


def test_password_policy_ok() -> None:
    validate_password_policy("abc12345")  # no raise


def test_role_rank_ordering() -> None:
    assert (
        role_rank(Role.OWNER)
        > role_rank(Role.ADMIN)
        > role_rank(Role.EDITOR)
        > role_rank(Role.VIEWER)
    )


async def test_require_role_forbidden() -> None:
    dep = require_role(Role.ADMIN)
    viewer: dict[str, Any] = {"id": 1, "username": "v", "role": "Viewer"}
    with pytest.raises(HTTPException) as ei:
        await dep(current_user=viewer)
    assert ei.value.status_code == 403


async def test_require_role_allowed() -> None:
    dep = require_role(Role.ADMIN)
    owner: dict[str, Any] = {"id": 1, "username": "o", "role": "Owner"}
    result = await dep(current_user=owner)
    assert result["role"] == "Owner"


def test_jwt_roundtrip(tmp_path: Path) -> None:
    os.environ["HERMES_CONSOLE_JWT_SECRET"] = "unit-test-secret-at-least-32-char-pad"
    import app.config as c

    c._settings = None
    from app.auth.jwt_utils import decode_token, encode_access_token

    token, ttl = encode_access_token("42", "Owner")
    assert ttl == 7200
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "Owner"
    assert payload["type"] == "access"


def test_fernet_creates_and_refuses_bad_perms(tmp_path: Path) -> None:
    key_path = tmp_path / "master.key"
    os.environ["HERMES_CONSOLE_MASTER_KEY_PATH"] = str(key_path)
    os.environ["HERMES_CONSOLE_JWT_SECRET"] = "unit-test-secret-at-least-32-char-pad"
    import app.auth.crypto as cr
    import app.config as c

    c._settings = None
    cr._fernet = None

    f = cr.get_fernet()
    assert key_path.exists()
    assert oct(key_path.stat().st_mode)[-3:] == "600"
    assert f.decrypt(f.encrypt(b"hi")) == b"hi"

    # tamper perms → next get_fernet must fail
    cr._fernet = None
    key_path.chmod(0o644)
    with pytest.raises(RuntimeError, match="insecure mode"):
        cr.get_fernet()
