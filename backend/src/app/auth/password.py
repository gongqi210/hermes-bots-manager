from __future__ import annotations

import re

from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

# Phase 1: bcrypt-only (argon2 optional extra not installed; user: "搞这么多安全干嘛").
# `PasswordHash.recommended()` would require argon2; stick with bcrypt per pyproject pin.
_pwd = PasswordHash((BcryptHasher(),))

_LETTER = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"[0-9]")


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd.verify(plain, hashed)
    except Exception:
        return False


def validate_password_policy(plain: str) -> None:
    """Simple 8+ alphanumeric rule per user decision ("搞这么多安全干嘛" — defer complexity)."""
    if len(plain) < 8:
        raise ValueError("密码至少 8 位")
    if not _LETTER.search(plain):
        raise ValueError("密码必须包含字母")
    if not _DIGIT.search(plain):
        raise ValueError("密码必须包含数字")
