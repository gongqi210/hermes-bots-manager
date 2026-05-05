"""Tests for LocalHostOps — the only HostOps impl in M1.

Covers (per Plan 02-01 Task 3 behavior spec):
  1. Clean exit-zero invocation
  2. stdout + stderr capture
  3. Timeout returns returncode=-9 with stderr="timeout"
  4. extra_env propagated to child process
  5. Arg whitelist rejects unknown flag (raises ValueError)
  6. Atomic write produces file with mode 0o600
  7. Atomic write does not leave .tmp behind
  8. path_exists toggles after remove_path
  9. list_dir returns sorted basenames
 10. get_process_info(self pid) returns is_alive=True
 11. get_process_info(unused pid) returns None
 12. run_hermes uses asyncio.create_subprocess_exec (not subprocess.run, no shell=True)
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters.hostops import CliResult, ProcessInfo
from app.adapters.local_hostops import LocalHostOps


def _make_stub(tmp_path: Path, body: str) -> Path:
    """Write a chmod-+x bash script and return its path."""
    script = tmp_path / "stub.sh"
    script.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


# --------------------------------------------------------------------------- #
# 1. clean exit zero                                                          #
# --------------------------------------------------------------------------- #
async def test_run_hermes_returns_clean_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _make_stub(tmp_path, "exit 0")
    monkeypatch.setattr(LocalHostOps, "HERMES_BINARY", str(stub))
    result = await LocalHostOps().run_hermes([])
    assert isinstance(result, CliResult)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


# --------------------------------------------------------------------------- #
# 2. stdout + stderr capture                                                  #
# --------------------------------------------------------------------------- #
async def test_run_hermes_captures_stdout_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _make_stub(tmp_path, "echo hi; echo err >&2")
    monkeypatch.setattr(LocalHostOps, "HERMES_BINARY", str(stub))
    result = await LocalHostOps().run_hermes([])
    assert result.returncode == 0
    assert "hi" in result.stdout
    assert "err" in result.stderr


# --------------------------------------------------------------------------- #
# 3. timeout returns -9                                                       #
# --------------------------------------------------------------------------- #
async def test_run_hermes_handles_timeout_returns_minus9(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _make_stub(tmp_path, "sleep 5")
    monkeypatch.setattr(LocalHostOps, "HERMES_BINARY", str(stub))
    result = await LocalHostOps().run_hermes([], timeout_sec=0.2)
    assert result.returncode == -9
    assert "timeout" in result.stderr


# --------------------------------------------------------------------------- #
# 4. extra_env propagated                                                     #
# --------------------------------------------------------------------------- #
async def test_run_hermes_extra_env_propagated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _make_stub(tmp_path, 'echo "$HERMES_HOME"')
    monkeypatch.setattr(LocalHostOps, "HERMES_BINARY", str(stub))
    result = await LocalHostOps().run_hermes([], extra_env={"HERMES_HOME": "/tmp/x"})
    assert result.returncode == 0
    assert "/tmp/x" in result.stdout


# --------------------------------------------------------------------------- #
# 5. arg whitelist rejects unknown flag                                       #
# --------------------------------------------------------------------------- #
async def test_run_hermes_rejects_unknown_flag() -> None:
    with pytest.raises(ValueError, match="refused arg"):
        await LocalHostOps().run_hermes(["--evil-flag"])


# --------------------------------------------------------------------------- #
# 6. atomic write — mode 0o600                                                #
# --------------------------------------------------------------------------- #
async def test_write_text_atomic_creates_file_with_mode_600(tmp_path: Path) -> None:
    target = tmp_path / "secret.env"
    await LocalHostOps().write_text_atomic(target, "FOO=bar\n")
    assert target.read_text(encoding="utf-8") == "FOO=bar\n"
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"


# --------------------------------------------------------------------------- #
# 7. atomic write — no leftover .tmp                                          #
# --------------------------------------------------------------------------- #
async def test_write_text_atomic_does_not_leave_tmp_after_success(tmp_path: Path) -> None:
    target = tmp_path / "secret.env"
    await LocalHostOps().write_text_atomic(target, "K=v")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"unexpected leftovers: {leftovers}"


# --------------------------------------------------------------------------- #
# 8. path_exists / remove_path                                                #
# --------------------------------------------------------------------------- #
async def test_path_exists_returns_true_then_false(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x")
    host = LocalHostOps()
    assert await host.path_exists(f) is True
    await host.remove_path(f)
    assert await host.path_exists(f) is False


# --------------------------------------------------------------------------- #
# 9. list_dir returns sorted basenames                                        #
# --------------------------------------------------------------------------- #
async def test_list_dir_returns_basenames_only(tmp_path: Path) -> None:
    for n in ("c", "a", "b"):
        (tmp_path / n).write_text("")
    host = LocalHostOps()
    assert await host.list_dir(tmp_path) == ["a", "b", "c"]


# --------------------------------------------------------------------------- #
# 10. get_process_info(self) returns is_alive=True                            #
# --------------------------------------------------------------------------- #
def test_get_process_info_for_self_pid_returns_alive() -> None:
    info = LocalHostOps().get_process_info(os.getpid())
    assert info is not None
    assert isinstance(info, ProcessInfo)
    assert info.pid == os.getpid()
    assert info.is_alive is True


# --------------------------------------------------------------------------- #
# 11. get_process_info(nonexistent) returns None                              #
# --------------------------------------------------------------------------- #
def test_get_process_info_for_nonexistent_pid_returns_none() -> None:
    info = LocalHostOps().get_process_info(999_999)
    assert info is None


# --------------------------------------------------------------------------- #
# 12. run_hermes uses asyncio.create_subprocess_exec                          #
# --------------------------------------------------------------------------- #
async def test_run_hermes_uses_create_subprocess_exec_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=0)

    spy = AsyncMock(return_value=mock_proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    monkeypatch.setattr(LocalHostOps, "HERMES_BINARY", "hermes")
    await LocalHostOps().run_hermes([])

    assert spy.await_count == 1
    args, kwargs = spy.call_args
    # First positional arg must be the binary path; no shell=True ever.
    assert args[0] == "hermes"
    assert kwargs.get("shell", False) is False
    assert "shell" not in kwargs or kwargs["shell"] is False


# --------------------------------------------------------------------------- #
# Bonus: read_text round-trip (sanity)                                        #
# --------------------------------------------------------------------------- #
async def test_read_text_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "r.txt"
    p.write_text("hello\n", encoding="utf-8")
    text = await LocalHostOps().read_text(p)
    assert text == "hello\n"


# --------------------------------------------------------------------------- #
# 13. run_command — arbitrary command (no whitelist), used for pip ops        #
# --------------------------------------------------------------------------- #
async def test_run_command_returns_stdout_for_simple_command(tmp_path: Path) -> None:
    """run_command bypasses the hermes binary and arg whitelist (Phase 3)."""
    result = await LocalHostOps().run_command(["echo", "hello"])
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr == ""


async def test_run_command_does_not_apply_hermes_flag_whitelist(tmp_path: Path) -> None:
    """pip args like '-m', '--version' are NOT on the hermes whitelist; should pass through."""
    # Use python3 -c to keep the test platform-agnostic.
    result = await LocalHostOps().run_command(["python3", "-c", "print('ok')"])
    assert result.returncode == 0
    assert "ok" in result.stdout


async def test_run_command_handles_timeout_returns_minus9(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _make_stub(tmp_path, "sleep 5")
    result = await LocalHostOps().run_command([str(stub)], timeout_sec=0.2)
    assert result.returncode == -9
    assert "timeout" in result.stderr


# --------------------------------------------------------------------------- #
# 14. InMemoryHostOps.run_command — fakes parity                              #
# --------------------------------------------------------------------------- #
async def test_in_memory_run_command_returns_queued_response() -> None:
    """InMemoryHostOps.run_command returns the queued CliResult and records call."""
    from tests.adapters.fakes import InMemoryHostOps

    host = InMemoryHostOps()
    host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(0, "Version: 1.5.5\n", ""),
    )
    result = await host.run_command(["python3", "-m", "pip", "show", "lark-oapi"])
    assert result.returncode == 0
    assert "1.5.5" in result.stdout
    # Recorded in command_calls (NOT in calls — those are hermes-binary calls).
    assert host.command_calls[0][0] == ["python3", "-m", "pip", "show", "lark-oapi"]
    assert host.calls == []


async def test_in_memory_run_command_returns_default_when_no_response_queued() -> None:
    from tests.adapters.fakes import InMemoryHostOps

    host = InMemoryHostOps()
    host.set_default_command_response(CliResult(1, "", "not found"))
    result = await host.run_command(["python3", "-m", "pip", "show", "ghost"])
    assert result.returncode == 1
    assert result.stderr == "not found"


async def test_in_memory_run_command_calls_separate_from_run_hermes_calls() -> None:
    """run_command and run_hermes record into different lists (queue isolation)."""
    from tests.adapters.fakes import InMemoryHostOps

    host = InMemoryHostOps()
    host.queue_response(["profile", "list"], CliResult(0, "hermes-out", ""))
    host.queue_command_response(["python3", "-V"], CliResult(0, "Python 3.12", ""))

    h_result = await host.run_hermes(["profile", "list"])
    c_result = await host.run_command(["python3", "-V"])
    assert h_result.stdout == "hermes-out"
    assert c_result.stdout == "Python 3.12"

    # Each invocation went to its own log.
    assert len(host.calls) == 1
    assert host.calls[0][0] == ["profile", "list"]
    assert len(host.command_calls) == 1
    assert host.command_calls[0][0] == ["python3", "-V"]


# Placate F401 on Any import (used by future fixtures expansion).
_ = Any
