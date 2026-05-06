from __future__ import annotations

import logging
import re

# NFR-02: scrub Feishu App Secret / cli_* fingerprints from every log record.
# Pattern 1: `cli_` + 8+ alnum chars (Feishu-style App ID fingerprint).
# Pattern 2: 40-char hex (App Secret length). Pattern 3: 32-char hex (token).
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"cli_[0-9a-zA-Z]{8,}"),
    re.compile(r"\b[0-9a-fA-F]{40}\b"),
    re.compile(r"\b[0-9a-fA-F]{32}\b"),
)


def _replace(match: re.Match[str]) -> str:
    return "cli_****" if match.group(0).startswith("cli_") else "****"


def scrub_secrets(text: str) -> str:
    """Return ``text`` with known credential fingerprints redacted."""
    for p in _PATTERNS:
        text = p.sub(_replace, text)
    return text


class SecretFilter(logging.Filter):
    """Redact secrets on both raw record.msg/args and exc_text (install on root logger)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = scrub_secrets(record.msg)
            if record.args:
                record.args = tuple(
                    scrub_secrets(str(a)) if isinstance(a, str) else a for a in record.args
                )
            if record.exc_text:
                record.exc_text = scrub_secrets(record.exc_text)
        except Exception:  # never break logging
            pass
        return True
