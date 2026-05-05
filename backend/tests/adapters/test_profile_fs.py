"""Unit tests for ProfileFsAdapter + validate_bot_name (Plan 02-03 Task 1).

Uses InMemoryHostOps (Plan 02-02) — no real filesystem touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.profile_fs import ProfileFsAdapter, validate_bot_name
from tests.adapters.fakes import InMemoryHostOps

# ---- profile_dir special case (Pitfall #2) -----------------------------------


def test_profile_dir_for_default_returns_hermes_home_root() -> None:
    """'default' profile lives at ~/.hermes/ root, NOT ~/.hermes/profiles/default/."""
    adapter = ProfileFsAdapter(InMemoryHostOps(), hermes_home=Path("/h"))
    assert adapter.profile_dir("default") == Path("/h")


def test_profile_dir_for_named_returns_profiles_subdir() -> None:
    """Named profiles live under ~/.hermes/profiles/<name>/."""
    adapter = ProfileFsAdapter(InMemoryHostOps(), hermes_home=Path("/h"))
    assert adapter.profile_dir("alpha") == Path("/h/profiles/alpha")


# ---- list_profiles -----------------------------------------------------------


async def test_list_profiles_includes_default_when_config_yaml_present() -> None:
    """Hermes creates ~/.hermes/config.yaml on first install — that's our 'default exists' marker."""
    host = InMemoryHostOps()
    host.fs[Path("/h/config.yaml")] = "version: 1\n"
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert "default" in await adapter.list_profiles()


async def test_list_profiles_excludes_default_when_config_yaml_missing() -> None:
    """No config.yaml → no default profile."""
    host = InMemoryHostOps()
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert "default" not in await adapter.list_profiles()


async def test_list_profiles_includes_named_profiles_from_subdir() -> None:
    """Named profiles discovered by listing ~/.hermes/profiles/."""
    host = InMemoryHostOps()
    host.fs[Path("/h/config.yaml")] = "version: 1\n"
    host.fs[Path("/h/profiles/alpha/SOUL.md")] = "alpha"
    host.fs[Path("/h/profiles/beta/SOUL.md")] = "beta"
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert await adapter.list_profiles() == ["alpha", "beta", "default"]


async def test_list_profiles_returns_sorted() -> None:
    """Output is alphabetical regardless of underlying iteration order."""
    host = InMemoryHostOps()
    host.fs[Path("/h/profiles/zebra/SOUL.md")] = "z"
    host.fs[Path("/h/profiles/alpha/SOUL.md")] = "a"
    host.fs[Path("/h/profiles/middle/SOUL.md")] = "m"
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    result = await adapter.list_profiles()
    assert result == sorted(result)
    assert result == ["alpha", "middle", "zebra"]


# ---- write_env (atomic + 0o600) ----------------------------------------------


async def test_write_env_writes_atomic_with_mode_600() -> None:
    """write_env routes through HostOps.write_text_atomic with mode=0o600.

    Spies on the InMemoryHostOps to capture the kwargs. Since the fake's
    write_text_atomic accepts mode but doesn't store it, we wrap it.
    """
    host = InMemoryHostOps()
    captured: dict[str, int] = {}
    original_write = host.write_text_atomic

    async def spy(path: Path, content: str, *, mode: int = 0o600) -> None:
        captured["mode"] = mode
        captured["path_str"] = str(path)  # type: ignore[assignment]
        captured["content_len"] = len(content)
        await original_write(path, content, mode=mode)

    host.write_text_atomic = spy  # type: ignore[method-assign]

    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    await adapter.write_env("alpha", {"FEISHU_APP_ID": "cli_xxx", "FEISHU_APP_SECRET": "yyy"})

    assert captured["mode"] == 0o600
    assert (
        host.fs[Path("/h/profiles/alpha/.env")] == "FEISHU_APP_ID=cli_xxx\nFEISHU_APP_SECRET=yyy\n"
    )


async def test_write_env_default_profile_writes_to_hermes_root() -> None:
    """Pitfall #2: 'default' .env is at ~/.hermes/.env not ~/.hermes/profiles/default/.env."""
    host = InMemoryHostOps()
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    await adapter.write_env("default", {"FEISHU_APP_ID": "cli_x"})
    assert Path("/h/.env") in host.fs
    assert Path("/h/profiles/default/.env") not in host.fs


async def test_write_env_escapes_special_chars() -> None:
    """MVP rejects multi-line values + '=' in keys (no shell-escape complexity)."""
    host = InMemoryHostOps()
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    with pytest.raises(ValueError, match="Invalid env entry"):
        await adapter.write_env("alpha", {"X": "line1\nline2"})
    with pytest.raises(ValueError, match="Invalid env entry"):
        await adapter.write_env("alpha", {"BAD=KEY": "value"})


# ---- read_env ----------------------------------------------------------------


async def test_read_env_returns_dict() -> None:
    """Round-trip: a properly-formatted .env yields a dict."""
    host = InMemoryHostOps()
    host.fs[Path("/h/profiles/alpha/.env")] = "FEISHU_APP_ID=cli\nFEISHU_APP_SECRET=secret\n"
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert await adapter.read_env("alpha") == {
        "FEISHU_APP_ID": "cli",
        "FEISHU_APP_SECRET": "secret",
    }


async def test_read_env_for_missing_returns_empty() -> None:
    """Missing .env is not an error — returns empty dict (Bot is unconfigured)."""
    host = InMemoryHostOps()
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert await adapter.read_env("alpha") == {}


async def test_read_env_skips_blank_and_comment_lines() -> None:
    """Bonus parse robustness: ignore blanks and # comments."""
    host = InMemoryHostOps()
    host.fs[Path("/h/profiles/alpha/.env")] = (
        "# comment line\n\nFEISHU_APP_ID=cli\n   \n# another\nFEISHU_APP_SECRET=secret\n"
    )
    adapter = ProfileFsAdapter(host, hermes_home=Path("/h"))
    assert await adapter.read_env("alpha") == {
        "FEISHU_APP_ID": "cli",
        "FEISHU_APP_SECRET": "secret",
    }


# ---- validate_bot_name (BOT-08) ----------------------------------------------


def test_validate_bot_name_accepts_lowercase_alphanum_dash_3_to_32() -> None:
    """Happy path + length boundaries + character class enforcement."""
    assert validate_bot_name("ab-c") == "ab-c"
    assert validate_bot_name("abc") == "abc"  # min length
    assert validate_bot_name("a" * 32) == "a" * 32  # max length
    assert validate_bot_name("test-bot-123") == "test-bot-123"

    with pytest.raises(ValueError, match="3-32"):
        validate_bot_name("a")  # too short
    with pytest.raises(ValueError, match="3-32"):
        validate_bot_name("ab")  # too short
    with pytest.raises(ValueError, match="3-32"):
        validate_bot_name("a" * 33)  # too long
    with pytest.raises(ValueError, match="3-32"):
        validate_bot_name("A-B")  # uppercase rejected
    with pytest.raises(ValueError, match="3-32"):
        validate_bot_name("a_b")  # underscore rejected (stricter than Hermes)


def test_validate_bot_name_rejects_default() -> None:
    """'default' is a reserved Hermes profile — Bot can't use it (Pitfall #2)."""
    with pytest.raises(ValueError, match="default"):
        validate_bot_name("default")


def test_validate_bot_name_rejects_starting_dash() -> None:
    """Pitfall #14: leading '-' would be parsed as flag if it ever reached subprocess."""
    with pytest.raises(ValueError, match="3-32"):
        validate_bot_name("-abc")
    with pytest.raises(ValueError, match="3-32"):
        validate_bot_name("-foo-bar")
