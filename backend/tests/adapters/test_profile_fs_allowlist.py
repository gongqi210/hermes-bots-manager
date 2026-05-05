"""Tests for ProfileFsAdapter allowlist methods (Plan 04-03 Task 2).

FINDING-04: ``FEISHU_ALLOWED_USERS`` is comma-separated single-line value.
Adapter trims whitespace, dedupes, preserves other keys, rejects entries with
the separator or newlines.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.profile_fs import ProfileFsAdapter
from tests.adapters.fakes import InMemoryHostOps

# ---- A1: read returns parsed list --------------------------------------------


async def test_read_allowed_users_parses_comma_separated() -> None:
    """FINDING-04 separator is comma."""
    host = InMemoryHostOps()
    host.fs[Path("/h/profiles/foo/.env")] = (
        "FEISHU_APP_ID=cli\nFEISHU_ALLOWED_USERS=ou_a,ou_b,ou_c\n"
    )
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert await adapter.read_allowed_users("foo") == ["ou_a", "ou_b", "ou_c"]


async def test_read_allowed_users_trims_whitespace_around_entries() -> None:
    """Permissive read — leading/trailing whitespace silently trimmed."""
    host = InMemoryHostOps()
    host.fs[Path("/h/profiles/foo/.env")] = "FEISHU_ALLOWED_USERS=ou_a , ou_b , ou_c\n"
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert await adapter.read_allowed_users("foo") == ["ou_a", "ou_b", "ou_c"]


# ---- A2: missing key returns [] ----------------------------------------------


async def test_read_allowed_users_returns_empty_when_key_missing() -> None:
    host = InMemoryHostOps()
    host.fs[Path("/h/profiles/foo/.env")] = "FEISHU_APP_ID=cli\n"
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert await adapter.read_allowed_users("foo") == []


async def test_read_allowed_users_returns_empty_when_key_value_blank() -> None:
    host = InMemoryHostOps()
    host.fs[Path("/h/profiles/foo/.env")] = "FEISHU_ALLOWED_USERS=\n"
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert await adapter.read_allowed_users("foo") == []


async def test_read_allowed_users_returns_empty_when_env_missing() -> None:
    host = InMemoryHostOps()
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert await adapter.read_allowed_users("foo") == []


# ---- A3: write preserves other keys ------------------------------------------


async def test_write_allowed_users_preserves_other_keys() -> None:
    host = InMemoryHostOps()
    host.fs[Path("/h/profiles/foo/.env")] = "FEISHU_APP_ID=cli_xxx\nFEISHU_APP_SECRET=secret-yyy\n"
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))

    await adapter.write_allowed_users("foo", ["ou_a", "ou_b"])

    written = host.fs[Path("/h/profiles/foo/.env")]
    assert "FEISHU_APP_ID=cli_xxx" in written
    assert "FEISHU_APP_SECRET=secret-yyy" in written
    assert "FEISHU_ALLOWED_USERS=ou_a,ou_b" in written


# ---- A4: dedupe + trim -------------------------------------------------------


async def test_write_allowed_users_dedupes_and_trims() -> None:
    """Input ['ou_a', ' ou_a ', 'ou_b'] → 'ou_a,ou_b' (first occurrence wins)."""
    host = InMemoryHostOps()
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))

    await adapter.write_allowed_users("foo", ["ou_a", " ou_a ", "ou_b"])

    written = host.fs[Path("/h/profiles/foo/.env")]
    assert "FEISHU_ALLOWED_USERS=ou_a,ou_b" in written


async def test_write_allowed_users_drops_blank_entries() -> None:
    host = InMemoryHostOps()
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))

    await adapter.write_allowed_users("foo", ["", "  ", "ou_a"])

    written = host.fs[Path("/h/profiles/foo/.env")]
    assert "FEISHU_ALLOWED_USERS=ou_a" in written


# ---- A5: reject separator / newline in entries -------------------------------


async def test_write_allowed_users_rejects_entry_containing_comma() -> None:
    host = InMemoryHostOps()
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))

    with pytest.raises(ValueError, match="invalid allowlist entry"):
        await adapter.write_allowed_users("foo", ["ou_a,smuggled"])


async def test_write_allowed_users_rejects_entry_containing_newline() -> None:
    host = InMemoryHostOps()
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))

    with pytest.raises(ValueError, match="invalid allowlist entry"):
        await adapter.write_allowed_users("foo", ["ou_a\nsmuggled"])


# ---- Round-trip --------------------------------------------------------------


async def test_write_then_read_round_trip() -> None:
    """Symmetry: read after write returns the deduped trimmed list."""
    host = InMemoryHostOps()
    host.fs[Path("/h/profiles/foo/.env")] = "FEISHU_APP_ID=cli\n"
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))

    await adapter.write_allowed_users("foo", ["ou_a", "ou_b", "ou_a"])
    assert await adapter.read_allowed_users("foo") == ["ou_a", "ou_b"]
