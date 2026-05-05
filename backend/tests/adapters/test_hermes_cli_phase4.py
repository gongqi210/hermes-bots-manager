"""Tests for the Phase 4 extension of HermesCliAdapter + HostOps probes.

Plan 04-03 Task 1. All tests use ``InMemoryHostOps`` — no real subprocess /
filesystem touched. Fixtures from 04-01 (Hermes v0.8 captures + UNAVAILABLE
markers) drive the parser/adapter contract.

Coverage:
  1. gateway_stop args + hint=gateway_stop_fail (no --all, scoped to profile)
  2. gateway_restart args + hint=gateway_restart_fail
  3. pairing_approve args + hint=pairing_expired (text classification, FINDING-05)
  4. pairing_approve input validation rejects bad code BEFORE subprocess call
  5. pairing_list parses 04-01 fixture into PairingListOutput
  6. gateway_log_path returns ~/.hermes/logs/gateway.log
  7. signal_zero(self pid) → True; signal_zero(99999999) → False (LocalHostOps)
  8. read_active_profile reads ~/.hermes/active_profile (file path)
  9. InMemoryHostOps fake supports scripted responses for new commands
 10. pairing_revoke args + input validation
 11. pairing_approve generic non-zero hint=pairing_approve_fail
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.adapters import (
    ApprovedUser,
    HermesCliAdapter,
    HermesCliError,
    LocalHostOps,
    PairingListOutput,
)
from app.adapters.hostops import CliResult, ProcessInfo
from tests.adapters.fakes import InMemoryHostOps

FIX = Path(__file__).parent.parent / "fixtures" / "hermes-cli"


# ---------------------------------------------------------------------------
# 1. gateway_stop
# ---------------------------------------------------------------------------


async def test_gateway_stop_calls_hermes_with_profile_scoped_args_no_all_flag() -> None:
    """FINDING-05: gateway stop must NOT pass --all (would tear down every Bot)."""
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "stopped", ""))
    adapter = HermesCliAdapter(host)

    await adapter.gateway_stop("foo")

    assert host.calls[-1][0] == ["-p", "foo", "gateway", "stop"]
    assert "--all" not in host.calls[-1][0]


async def test_gateway_stop_raises_with_hint_gateway_stop_fail_on_nonzero() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(1, "", "kill failed"))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.gateway_stop("foo")

    assert exc.value.hint == "gateway_stop_fail"
    assert exc.value.returncode == 1


# ---------------------------------------------------------------------------
# 2. gateway_restart
# ---------------------------------------------------------------------------


async def test_gateway_restart_calls_hermes_with_correct_args() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "restarted", ""))
    adapter = HermesCliAdapter(host)

    await adapter.gateway_restart("foo")

    assert host.calls[-1][0] == ["-p", "foo", "gateway", "restart"]


async def test_gateway_restart_raises_with_hint_gateway_restart_fail_on_nonzero() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(1, "", "could not restart"))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.gateway_restart("foo")

    assert exc.value.hint == "gateway_restart_fail"


# ---------------------------------------------------------------------------
# 3. pairing_approve — expired-code classification (FINDING-05)
# ---------------------------------------------------------------------------


async def test_pairing_approve_calls_hermes_with_correct_args() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "Approved", ""))
    adapter = HermesCliAdapter(host)

    await adapter.pairing_approve("foo", "ABC123")

    assert host.calls[-1][0] == ["-p", "foo", "pairing", "approve", "feishu", "ABC123"]


async def test_pairing_approve_classifies_not_found_or_expired_as_pairing_expired() -> None:
    """FINDING-05: 'not found or expired' phrase → hint=pairing_expired (even with exit 0)."""
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, (FIX / "pairing_approve_expired.txt").read_text(), ""))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.pairing_approve("foo", "TESTNOTREAL")

    assert exc.value.hint == "pairing_expired"


async def test_pairing_approve_generic_failure_hint_pairing_approve_fail() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(2, "internal error", "boom"))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.pairing_approve("foo", "ABC123")

    assert exc.value.hint == "pairing_approve_fail"


# ---------------------------------------------------------------------------
# 4. pairing_approve input validation BEFORE subprocess
# ---------------------------------------------------------------------------


async def test_pairing_approve_rejects_code_with_spaces_before_subprocess() -> None:
    """ValueError must raise before any host.calls entry is recorded."""
    host = InMemoryHostOps()
    adapter = HermesCliAdapter(host)

    with pytest.raises(ValueError, match="invalid pairing code"):
        await adapter.pairing_approve("foo", "BAD CODE WITH SPACES")

    assert host.calls == []


async def test_pairing_approve_rejects_overlong_code() -> None:
    host = InMemoryHostOps()
    adapter = HermesCliAdapter(host)

    with pytest.raises(ValueError, match="invalid pairing code"):
        await adapter.pairing_approve("foo", "A" * 65)

    assert host.calls == []


async def test_pairing_approve_rejects_empty_code() -> None:
    host = InMemoryHostOps()
    adapter = HermesCliAdapter(host)

    with pytest.raises(ValueError, match="invalid pairing code"):
        await adapter.pairing_approve("foo", "")


async def test_pairing_approve_rejects_non_alnum_code() -> None:
    host = InMemoryHostOps()
    adapter = HermesCliAdapter(host)

    with pytest.raises(ValueError, match="invalid pairing code"):
        await adapter.pairing_approve("foo", "ABC-123")


# ---------------------------------------------------------------------------
# 5. pairing_list — parser round-trip from 04-01 fixture
# ---------------------------------------------------------------------------


async def test_pairing_list_parses_04_01_fixture_into_dataclass() -> None:
    """Round-trip the no-pending+approved fixture (FINDING-05 fixture-gap shape)."""
    host = InMemoryHostOps()
    host.queue_response(
        ["-p", "foo", "pairing", "list"],
        CliResult(0, (FIX / "pairing_list_with_pending.txt").read_text(), ""),
    )
    adapter = HermesCliAdapter(host)

    result = await adapter.pairing_list("foo")

    assert isinstance(result, PairingListOutput)
    assert result.pending == []
    assert len(result.approved) == 2
    assert result.approved[0] == ApprovedUser(
        platform="feishu",
        user_id="ou_fixtureuser000000000001",
        name=None,
    )
    assert result.approved[1] == ApprovedUser(
        platform="feishu",
        user_id="ou_fixtureuser000000000002",
        name=None,
    )


# ---------------------------------------------------------------------------
# 6. gateway_log_path
# ---------------------------------------------------------------------------


def test_gateway_log_path_returns_hermes_logs_gateway_log() -> None:
    """Pure constant — Hermes v0.8 has one shared log file."""
    adapter = HermesCliAdapter(InMemoryHostOps())
    assert adapter.gateway_log_path() == Path.home() / ".hermes" / "logs" / "gateway.log"


def test_gateway_log_path_prefers_profile_specific_log_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hermes newer profile gateways write ~/.hermes/profiles/<name>/logs/gateway.log."""
    fake_home = tmp_path / "home"
    profile_log = fake_home / ".hermes" / "profiles" / "foo" / "logs" / "gateway.log"
    profile_log.parent.mkdir(parents=True)
    profile_log.write_text("profile log\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    adapter = HermesCliAdapter(InMemoryHostOps())

    assert adapter.gateway_log_path("foo") == profile_log


# ---------------------------------------------------------------------------
# 7. signal_zero (LocalHostOps)
# ---------------------------------------------------------------------------


async def test_signal_zero_for_self_pid_returns_true() -> None:
    """layer-3 liveness probe — kill(0) on this process succeeds."""
    host = LocalHostOps()
    assert await host.signal_zero(os.getpid()) is True


async def test_signal_zero_for_nonexistent_pid_returns_false() -> None:
    host = LocalHostOps()
    assert await host.signal_zero(99_999_999) is False


# ---------------------------------------------------------------------------
# 8. read_active_profile (LocalHostOps + InMemoryHostOps)
# ---------------------------------------------------------------------------


async def test_local_read_active_profile_from_plain_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File-first: ~/.hermes/active_profile as a plain text file."""
    fake_home = tmp_path / "home"
    (fake_home / ".hermes").mkdir(parents=True)
    (fake_home / ".hermes" / "active_profile").write_text("alpha\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    host = LocalHostOps()
    assert await host.read_active_profile() == "alpha"


async def test_local_read_active_profile_returns_none_when_no_file_and_cli_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file + no hermes binary on PATH → returns None gracefully."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    # Point HERMES_BINARY at /bin/false so the CLI fallback exits 1.
    monkeypatch.setattr(LocalHostOps, "HERMES_BINARY", "/bin/false")

    host = LocalHostOps()
    assert await host.read_active_profile() is None


async def test_in_memory_read_active_profile_returns_attr() -> None:
    """InMemoryHostOps exposes ``active_profile`` attribute for test scripting."""
    host = InMemoryHostOps()
    assert await host.read_active_profile() is None
    host.active_profile = "beta"
    assert await host.read_active_profile() == "beta"


# ---------------------------------------------------------------------------
# 9. InMemoryHostOps fake supports scripted responses for new commands
# ---------------------------------------------------------------------------


async def test_in_memory_host_supports_scripted_response_for_pairing_approve() -> None:
    """Existing queue mechanism should match Phase 4 args verbatim."""
    host = InMemoryHostOps()
    host.queue_response(
        ["-p", "foo", "pairing", "approve", "feishu", "ABC123"],
        CliResult(0, "Approved successfully\n", ""),
    )
    adapter = HermesCliAdapter(host)

    # No exception = approve succeeded; check args were recorded too.
    await adapter.pairing_approve("foo", "ABC123")
    assert host.calls[-1][0] == ["-p", "foo", "pairing", "approve", "feishu", "ABC123"]


async def test_in_memory_signal_zero_uses_alive_pids_set() -> None:
    """Test scripting: pids in ``alive_pids`` return True; others fall through."""
    host = InMemoryHostOps()
    host.alive_pids.add(4242)
    assert await host.signal_zero(4242) is True
    assert await host.signal_zero(9999) is False


async def test_in_memory_signal_zero_falls_back_to_process_table() -> None:
    """If pid is in process_table with is_alive=True, signal_zero returns True."""
    host = InMemoryHostOps()
    host.process_table[7777] = ProcessInfo(pid=7777, cmdline=[], environ={}, is_alive=True)
    assert await host.signal_zero(7777) is True


# ---------------------------------------------------------------------------
# 10. pairing_revoke — args + validation
# ---------------------------------------------------------------------------


async def test_pairing_revoke_calls_hermes_with_correct_args() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "Revoked", ""))
    adapter = HermesCliAdapter(host)

    await adapter.pairing_revoke("foo", "ou_xxx")

    assert host.calls[-1][0] == ["-p", "foo", "pairing", "revoke", "feishu", "ou_xxx"]


async def test_pairing_revoke_rejects_user_id_with_spaces() -> None:
    host = InMemoryHostOps()
    adapter = HermesCliAdapter(host)

    with pytest.raises(ValueError, match="invalid pairing user_id"):
        await adapter.pairing_revoke("foo", "ou xxx")
    assert host.calls == []


async def test_pairing_revoke_rejects_user_id_starting_with_dash() -> None:
    """Defense in depth: leading '-' would be parsed as flag if it ever reached subprocess."""
    host = InMemoryHostOps()
    adapter = HermesCliAdapter(host)

    with pytest.raises(ValueError, match="invalid pairing user_id"):
        await adapter.pairing_revoke("foo", "-evil")


async def test_pairing_revoke_propagates_not_found_hint() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(1, "User 'ghost' not found.", ""))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.pairing_revoke("foo", "ou_ghost")

    assert exc.value.hint == "not_found"
