"""Pure parser functions for Hermes CLI output. Golden-file tested against fixtures.

Hermes v0.8 has NO ``--json`` flag — every output is human-formatted. These
functions convert that into typed dataclasses. Adding a new parser MUST come
with a golden fixture under ``backend/tests/fixtures/hermes-cli/``.

This module is **pure**: no ``asyncio``, ``subprocess``, ``os``, or filesystem
access. Inputs are strings; outputs are dataclasses or simple primitives.
That keeps the test surface trivial and lets ``HermesCliAdapter`` (the only
caller) own the IO boundary.

Hermes v0.8 quirks worth knowing:

* ``profile list`` is a Unicode box-drawing table; we slice rows by **header
  column positions** because some names (e.g. ``test-research-probe``) are
  followed by only a single space before the next column.
* The U+2014 em-dash ("—") is the empty-cell placeholder.
* ``profile show`` is a ``Key: Value`` block; absent ``.env`` is rendered as
  ``not configured`` (Pitfall #3 / #4 in 02-RESEARCH.md).
* ``profile create`` errors print to **stdout** not stderr (Pitfall #3).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileSummary:
    """One row of ``hermes profile list`` output."""

    name: str
    model: str | None  # None when fixture shows the U+2014 placeholder
    gateway: str  # raw string: "running" | "stopped" | etc.
    alias: str | None
    is_active: bool  # leading ◆ marker


@dataclass(frozen=True)
class ProfileShow:
    """Parsed ``hermes profile show <name>`` output."""

    name: str
    path: str
    gateway: str
    skills: int
    env_configured: bool  # False when ".env: not configured"
    soul_md_exists: bool  # True when "SOUL.md: exists"
    alias: str | None  # absolute path or None


@dataclass(frozen=True)
class PendingPairing:
    """One pending pairing-code row (Phase 4 ``hermes pairing list``).

    The Phase 4 fixture currently has no pending rows (FINDING-05); this
    dataclass is parsed best-effort using the same column shape as the
    ``Approved Users`` table. ``created`` is the raw timestamp string from
    Hermes — leave parsing to the consumer (UI renders verbatim).
    """

    platform: str
    code: str
    created: str | None = None


@dataclass(frozen=True)
class ApprovedUser:
    """One row of the ``Approved Users`` section in ``hermes pairing list``."""

    platform: str
    user_id: str
    name: str | None = None


@dataclass(frozen=True)
class PairingListOutput:
    """Parsed ``hermes -p <p> pairing list`` output."""

    pending: list[PendingPairing]
    approved: list[ApprovedUser]


@dataclass(frozen=True)
class GatewayPidFile:
    """Parsed ``~/.hermes/gateway.pid`` JSON contents."""

    pid: int
    kind: str
    argv: list[str]
    start_time: str | None


_DASH = "—"  # U+2014 — Hermes' empty-cell placeholder
_PROFILE_LIST_HEADERS: tuple[str, ...] = ("Profile", "Model", "Gateway", "Alias")
_SEPARATOR_CHARS = frozenset("─ \t")


def parse_profile_list(stdout: str) -> list[ProfileSummary]:
    """Parse ``hermes profile list`` Unicode-table output.

    Header line + separator (─ chars) + data rows. The Hermes v0.8 table is
    space-padded but **not** a true fixed-column layout — long names like
    ``test-research-probe`` (19 chars) overflow the 17-char ``Profile`` column
    and crowd the next cell with a single space.

    Strategy:
      1. Split each row on 2+ whitespace.
      2. If we get 4 cells, use them.
      3. If we get 3 cells AND the first cell ends with ``" <single-char>"``
         (the em-dash placeholder), split it — that's the overflow case.
      4. Otherwise, skip (defensive — unknown row shape).
    """
    rows: list[ProfileSummary] = []

    for raw in stdout.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if all(h in raw for h in _PROFILE_LIST_HEADERS):
            continue
        if all(c in _SEPARATOR_CHARS for c in stripped):
            continue

        cells = _split_row(stripped)
        if cells is None:
            continue

        name_field = cells[0].strip()
        if not name_field or name_field in _PROFILE_LIST_HEADERS:
            continue
        is_active = name_field.startswith("◆")
        name = name_field.lstrip("◆").strip()

        model_cell = cells[1].strip()
        gateway = cells[2].strip()
        alias_cell = cells[3].strip()

        rows.append(
            ProfileSummary(
                name=name,
                model=None if model_cell in (_DASH, "") else model_cell,
                gateway=gateway,
                alias=None if alias_cell in (_DASH, "") else alias_cell,
                is_active=is_active,
            )
        )
    return rows


def _split_row(stripped: str) -> list[str] | None:
    """Split a data row into exactly 4 cells, handling the overflow case.

    Hermes v0.8 separates cells with 2+ spaces normally, but a name that
    overflows its column gets only 1 space before the next cell. When that
    happens the first whitespace split produces 3 cells with the form
    ``["<name> <em-dash>", "<gateway>", "<alias>"]`` — split the leading cell
    on its single space to recover the missing model cell.
    """
    parts = re.split(r"\s{2,}", stripped)
    if len(parts) >= 4:
        return parts[:4]
    if len(parts) == 3 and " " in parts[0]:
        head, _, tail = parts[0].rpartition(" ")
        if head and tail:
            return [head, tail, parts[1], parts[2]]
    return None


_SHOW_KEY_RE = re.compile(r"^([A-Za-z.]+):\s*(.*)$")


def parse_profile_show(stdout: str) -> ProfileShow:
    """Parse ``hermes profile show <name>`` ``Key: Value`` block output."""
    kv: dict[str, str] = {}
    for line in stdout.splitlines():
        m = _SHOW_KEY_RE.match(line.strip())
        if m:
            kv[m.group(1)] = m.group(2).strip()

    env_value = kv.get(".env", "")
    soul_value = kv.get("SOUL.md", "")
    skills_raw = kv.get("Skills", "0")
    try:
        skills_int = int(skills_raw)
    except ValueError:
        skills_int = 0

    alias_value = kv.get("Alias", "").strip()
    alias = None if alias_value in ("", _DASH, "none", "(none)") else alias_value

    return ProfileShow(
        name=kv.get("Profile", ""),
        path=kv.get("Path", ""),
        gateway=kv.get("Gateway", ""),
        skills=skills_int,
        env_configured=bool(env_value) and "not configured" not in env_value,
        soul_md_exists="exists" in soul_value,
        alias=alias,
    )


def classify_create_error(stdout: str) -> str:
    """Classify ``hermes profile create`` error output (errors emit to stdout, Pitfall #3).

    Returns one of: ``'duplicate'`` | ``'invalid_name'`` | ``'reserved_name'``
    | ``'unknown'``. Order of checks is significant — ``Cannot create a profile
    named 'default'`` is the most specific reserved-name signal so it must win
    over a generic ``built-in profile`` substring match.
    """
    if "already exists" in stdout:
        return "duplicate"
    if "Invalid profile name" in stdout:
        return "invalid_name"
    if "built-in profile" in stdout or "Cannot create a profile named 'default'" in stdout:
        return "reserved_name"
    return "unknown"


_PAIRING_PENDING_HEADER_RE = re.compile(r"Pending\s+Pairing\s+Requests?\b", re.IGNORECASE)
_PAIRING_NO_PENDING_RE = re.compile(r"No\s+pending\s+pairing\s+requests?\b", re.IGNORECASE)
_PAIRING_APPROVED_HEADER_RE = re.compile(r"Approved\s+Users?\b", re.IGNORECASE)


def _is_separator_line(stripped: str) -> bool:
    """A divider made entirely of dashes / spaces (header/body separator)."""
    return bool(stripped) and all(c in {"-", "─", " ", "\t"} for c in stripped)


def parse_pairing_list(stdout: str) -> PairingListOutput:
    """Parse ``hermes -p <p> pairing list`` human-formatted output.

    Empirical anchor (FINDING-05): the v0.8 layout is two sections:

      ``Pending Pairing Requests (N):``  -- with rows ``platform code [created]``
      ``Approved Users (N):``            -- with rows ``platform user_id [name]``

    Each section has a ``Platform / ...`` header line and a dashed separator.
    Either section may be absent; ``No pending pairing requests.`` is rendered
    in place of the pending header.

    Strategy: section-state machine. Cells are split on 2+ whitespace; rows
    with fewer than the minimum cells (platform + value) are silently skipped
    (defensive — log lines mid-output do not match the column shape).

    The Phase 4 fixture currently captures only the ``no-pending + 2 approved``
    shape. Pending-row parsing is therefore fixture-gap tolerant — when a real
    pending sample lands, extend the row matcher (no API change needed).
    """
    pending: list[PendingPairing] = []
    approved: list[ApprovedUser] = []

    section: str | None = None  # 'pending' | 'approved' | None
    skip_header_row = False  # set True after a section header so we skip its column row

    for raw in stdout.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue

        # Section transitions ---------------------------------------------------
        if _PAIRING_NO_PENDING_RE.search(stripped):
            section = None  # explicitly no pending
            skip_header_row = False
            continue
        if _PAIRING_PENDING_HEADER_RE.search(stripped):
            section = "pending"
            skip_header_row = True
            continue
        if _PAIRING_APPROVED_HEADER_RE.search(stripped):
            section = "approved"
            skip_header_row = True
            continue

        # Skip the column-name row (e.g. "Platform     User ID     Name").
        if skip_header_row and (
            stripped.lower().startswith("platform") or _is_separator_line(stripped)
        ):
            # Reset only after the dashed separator passes — both column row
            # and dashed row are skipped.
            if _is_separator_line(stripped):
                skip_header_row = False
            continue
        if _is_separator_line(stripped):
            continue

        cells = re.split(r"\s{2,}", stripped)
        # Single-space-separated rows (rare, but defensible) → fall back.
        if len(cells) < 2:
            cells = stripped.split()

        if section == "approved" and len(cells) >= 2:
            platform = cells[0].strip()
            user_id = cells[1].strip()
            name_cell = cells[2].strip() if len(cells) >= 3 else ""
            approved.append(
                ApprovedUser(
                    platform=platform,
                    user_id=user_id,
                    name=name_cell or None,
                )
            )
        elif section == "pending" and len(cells) >= 2:
            platform = cells[0].strip()
            code = cells[1].strip()
            created_cell = " ".join(cells[2:]).strip() if len(cells) >= 3 else ""
            pending.append(
                PendingPairing(
                    platform=platform,
                    code=code,
                    created=created_cell or None,
                )
            )

    return PairingListOutput(pending=pending, approved=approved)


def parse_gateway_pid_file(content: str) -> GatewayPidFile | None:
    """Parse ``~/.hermes/gateway.pid`` JSON. Returns ``None`` if malformed.

    Never raises — the supervisor in Plan 02-05 treats ``None`` as "PID file
    is corrupt; fall back to listing processes".
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return GatewayPidFile(
            pid=int(data["pid"]),
            kind=str(data.get("kind", "")),
            argv=list(data.get("argv", [])),
            start_time=data.get("start_time"),
        )
    except (KeyError, TypeError, ValueError):
        return None
