from __future__ import annotations

import logging
import stat
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import get_settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _ensure_master_key(path: Path) -> bytes:
    """Load the Fernet master key from `path`. Create on first run with chmod 600.

    Refuses to use an existing file whose mode != 0o600 (Pitfall #6).
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        key = Fernet.generate_key()
        path.write_bytes(key)
        path.chmod(0o600)
        logger.warning("Generated new Fernet master key at %s (mode 600)", path)
        return key

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise RuntimeError(
            f"Master key {path} has insecure mode 0o{mode:o} (must be 0o600). "
            "Run: chmod 600 " + str(path)
        )
    return path.read_bytes()


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = _ensure_master_key(get_settings().master_key_path)
        _fernet = Fernet(key)
    return _fernet


def encrypt_str(plain: str) -> str:
    return get_fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_str(token: str) -> str:
    return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
