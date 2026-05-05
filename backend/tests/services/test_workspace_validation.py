"""Tests for workspace path validation (Task 1 / WORKSPACE-03).

These tests use real filesystem operations via pytest tmp_path to ensure
the security checks work against actual Path.resolve() behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.management import validate_workspace_path


@pytest.fixture()
def hermes_home(tmp_path: Path) -> Path:
    h = tmp_path / ".hermes"
    h.mkdir()
    return h


def test_rejects_hermes_home(tmp_path: Path, hermes_home: Path) -> None:
    """Path inside ~/.hermes/ must be rejected with 422 and Chinese message."""
    inner = hermes_home / "profiles" / "mybot"
    inner.mkdir(parents=True)
    with pytest.raises(HTTPException) as exc_info:
        validate_workspace_path(str(inner), hermes_home)
    assert exc_info.value.status_code == 422
    assert "Hermes 家目录" in str(exc_info.value.detail)


def test_rejects_traversal(tmp_path: Path, hermes_home: Path) -> None:
    """Path containing .. components before resolve must be rejected."""
    cwd = str(tmp_path / "safe" / ".." / ".." / "etc")
    with pytest.raises(HTTPException) as exc_info:
        validate_workspace_path(cwd, hermes_home)
    assert exc_info.value.status_code == 422
    assert "路径穿越" in str(exc_info.value.detail)


def test_rejects_symlink_outside(tmp_path: Path, hermes_home: Path) -> None:
    """Symlink pointing outside the allowed zone must be rejected."""
    target = tmp_path / "outside"
    target.mkdir()
    link = tmp_path / "link_to_outside"
    link.symlink_to(target)
    with pytest.raises(HTTPException) as exc_info:
        validate_workspace_path(str(link), hermes_home)
    assert exc_info.value.status_code == 422
    assert "符号链接" in str(exc_info.value.detail)


def test_valid_absolute_path(tmp_path: Path, hermes_home: Path) -> None:
    """Valid absolute path outside ~/.hermes/ that exists and is writable passes."""
    safe = tmp_path / "workspace"
    safe.mkdir()
    # Should not raise
    validate_workspace_path(str(safe), hermes_home)


def test_nonexistent_path_passes(tmp_path: Path, hermes_home: Path) -> None:
    """Non-existent path passes validation (existence is checked by probe, not validate)."""
    nonexistent = tmp_path / "does_not_exist"
    # Should not raise
    validate_workspace_path(str(nonexistent), hermes_home)
