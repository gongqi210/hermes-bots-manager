"""Pairing-code extractor — Phase 4 GATEWAY-10.

Pure function: takes a single line of ``~/.hermes/logs/gateway.log``, returns
a :class:`PairingCandidate` if the line carries a Feishu pairing-code request,
else ``None``. Caller (``GatewaySupervisor``) is responsible for filtering
lines by active profile BEFORE calling :func:`extract_pairing` — the singleton
gateway broadcasts all profiles' lines to one log file (FINDING-03).

Source of truth for the regex
-----------------------------

``backend/tests/fixtures/hermes-cli/HERMES_V08_FINDINGS.md`` FINDING-01 is the
single source of truth for the pairing-line shape. As of the Wave 0 capture
(2026-05-04) FINDING-01 is **UNAVAILABLE** — no live Feishu pairing event was
present in the workspace, so the literal regex cannot yet be derived from a
real log line. This module ships with the documented fallback regex from the
plan and exposes ``_PAIRING_FALLBACK = True``; once a real fixture lands, the
regex constant is updated and the flag flipped to False.

The fallback regex is::

    pairing.*?code[\\s:=]+([A-Za-z0-9]{4,12})

Captured group 1 = pairing code (alphanumeric, 4-12 chars). Case-insensitive.

When ``_PAIRING_FALLBACK`` is True, every ``extract_pairing`` invocation that
returns a candidate emits a ``WARNING`` log entry noting the fallback regex is
in use — the operations team can grep audit logs to confirm fixture-update
cadence.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# FINDING-01 regex — derived from
# ``backend/tests/fixtures/hermes-cli/pairing_log_sample.txt``.
#
# As of 2026-05-04 the fixture is UNAVAILABLE (no real pairing event was
# captured). We use the documented fallback regex from the plan and set
# ``_PAIRING_FALLBACK = True``. Replace both when a real sample lands and
# update HERMES_V08_FINDINGS.md FINDING-01 accordingly.
_PAIRING_RE = re.compile(r"pairing.*?code[\s:=]+([A-Za-z0-9]{4,12})", re.IGNORECASE)
_PAIRING_FALLBACK = True

# Feishu OpenID — ``ou_`` prefix + at least 20 alphanumeric chars. Used to
# enrich the candidate with the requesting user when present in the same line.
_USER_ID_RE = re.compile(r"\b(ou_[a-zA-Z0-9]{20,})\b")

# One-shot fallback warning per process — keeps the log line out of the hot
# path while still surfacing the fixture gap.
_warned_fallback = False


@dataclass(frozen=True)
class PairingCandidate:
    """A pairing-code observation that has not yet been persisted.

    Attributes:
        code: Plaintext pairing code (alphanumeric, 4-12 chars under fallback).
        feishu_user_id: Feishu OpenID of the requesting user, if extractable.
        bot_name: Hermes profile name the candidate belongs to (caller-supplied
            via the active-profile check — see FINDING-03).
        platform: Pairing platform — fixed to ``feishu`` in MVP.
    """

    code: str
    feishu_user_id: str | None
    bot_name: str
    platform: str = "feishu"


def extract_pairing(line: str, *, bot_name: str) -> PairingCandidate | None:
    """Return a :class:`PairingCandidate` if ``line`` carries a pairing code.

    Pure function — does NOT touch DB / disk / logger (other than the
    one-shot fallback notice). Caller is responsible for per-Bot profile
    filtering BEFORE invoking this so the wrong Bot does not absorb a code
    bound to a different profile (FINDING-03).
    """
    match = _PAIRING_RE.search(line)
    if match is None:
        return None
    # Support both named and unnamed capturing groups so a future regex
    # update with ``(?P<code>...)`` works without source changes here.
    groups = match.groupdict()
    code = groups["code"] if "code" in groups else match.group(1)

    user_match = _USER_ID_RE.search(line)

    global _warned_fallback
    if _PAIRING_FALLBACK and not _warned_fallback:
        logger.warning(
            "pairing_extractor using FALLBACK regex (FINDING-01 unavailable);"
            " update HERMES_V08_FINDINGS.md once a real fixture lands"
        )
        _warned_fallback = True

    return PairingCandidate(
        code=code,
        feishu_user_id=user_match.group(1) if user_match else None,
        bot_name=bot_name,
    )
