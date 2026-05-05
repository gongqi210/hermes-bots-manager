"""Tests for ProfileFsAdapter.snapshot_profile (Task 2).

Uses real filesystem via tmp_path + LocalHostOps for accurate symlink/path behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.local_hostops import LocalHostOps
from app.adapters.profile_fs import ProfileFsAdapter


def _make_adapter(hermes_home: Path) -> ProfileFsAdapter:
    return ProfileFsAdapter(host=LocalHostOps(), hermes_home=hermes_home)


def _make_profile(hermes_home: Path, name: str) -> Path:
    profile_dir = hermes_home / "profiles" / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    # Minimal config.yaml
    (profile_dir / "config.yaml").write_text("model:\n  provider: openai\n")
    # .env with secrets
    (profile_dir / ".env").write_text(
        "FEISHU_APP_ID=cli_abc\n"
        "FEISHU_APP_SECRET=super_secret_value\n"
        "OPENAI_API_KEY=sk-1234567890\n"
        "NORMAL_VAR=hello\n"
    )
    return profile_dir


@pytest.mark.asyncio
async def test_snapshot_creates_directory(tmp_path: Path) -> None:
    """snapshot_profile creates a .snapshots/<timestamp>/ dir with config.yaml and .env."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    _make_profile(hermes_home, "mybot")

    adapter = _make_adapter(hermes_home)
    snap_path = await adapter.snapshot_profile("mybot")

    assert snap_path.exists()
    assert snap_path.is_dir()
    assert (snap_path / "config.yaml").exists()
    assert (snap_path / ".env").exists()


@pytest.mark.asyncio
async def test_snapshot_redacts_secrets(tmp_path: Path) -> None:
    """Secrets in .env snapshot have values replaced with ***."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    _make_profile(hermes_home, "mybot")

    adapter = _make_adapter(hermes_home)
    snap_path = await adapter.snapshot_profile("mybot")

    env_content = (snap_path / ".env").read_text()
    assert "super_secret_value" not in env_content
    assert "sk-1234567890" not in env_content
    assert "FEISHU_APP_SECRET=***" in env_content
    assert "OPENAI_API_KEY=***" in env_content
    # Non-sensitive vars preserved
    assert "NORMAL_VAR=hello" in env_content
    assert "FEISHU_APP_ID=cli_abc" in env_content


@pytest.mark.asyncio
async def test_snapshot_retention_10(tmp_path: Path) -> None:
    """After 11 snapshots, oldest is deleted; only 10 kept."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    _make_profile(hermes_home, "mybot")

    adapter = _make_adapter(hermes_home)
    for _ in range(11):
        await adapter.snapshot_profile("mybot")

    snapshots_dir = hermes_home / "profiles" / "mybot" / ".snapshots"
    remaining = sorted(snapshots_dir.iterdir())
    assert len(remaining) == 10


@pytest.mark.asyncio
async def test_snapshot_no_soul_md_ok(tmp_path: Path) -> None:
    """Snapshot completes without error when SOUL.md does not exist."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    _make_profile(hermes_home, "mybot")
    # No SOUL.md created

    adapter = _make_adapter(hermes_home)
    snap_path = await adapter.snapshot_profile("mybot")

    assert snap_path.exists()
    assert not (snap_path / "SOUL.md").exists()
