from __future__ import annotations

import logging
from typing import Any

from app.secret_filter import SecretFilter, scrub_secrets


def _make_record(msg: str, args: tuple[Any, ...] = ()) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_scrubs_cli_fingerprint() -> None:
    f = SecretFilter()
    rec = _make_record("Using app_id cli_0123456789abcdefXYZ in request")
    f.filter(rec)
    assert "cli_0123456789abcdefXYZ" not in rec.getMessage()
    assert "cli_****" in rec.getMessage()


def test_scrubs_40_hex_secret() -> None:
    f = SecretFilter()
    secret = "a" * 40  # 40 hex chars
    rec = _make_record(f"Feishu secret leaked: {secret}")
    f.filter(rec)
    assert secret not in rec.getMessage()
    assert "****" in rec.getMessage()


def test_passes_short_strings_unchanged() -> None:
    f = SecretFilter()
    rec = _make_record("short cli_abc message")
    f.filter(rec)
    assert rec.getMessage() == "short cli_abc message"


def test_scrubs_in_args() -> None:
    f = SecretFilter()
    rec = _make_record("secret=%s", ("cli_0123456789abcdef",))
    f.filter(rec)
    assert "cli_0123456789abcdef" not in rec.getMessage()


def test_scrub_secrets_can_sanitize_external_log_lines() -> None:
    secret = "b" * 40
    text = scrub_secrets(f"gateway app=cli_abcDEF1234567890 secret={secret}")
    assert "cli_abcDEF1234567890" not in text
    assert secret not in text
    assert "cli_****" in text
    assert "****" in text
