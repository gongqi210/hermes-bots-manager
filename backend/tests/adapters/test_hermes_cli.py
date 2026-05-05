"""Tests for HermesCliAdapter — the single entry point for ``hermes`` invocations.

Uses ``InMemoryHostOps`` from ``fakes.py`` so we never spawn a real subprocess.
Every test asserts on either ``host.calls`` (correct args constructed) or on
the typed ``HermesCliError.hint`` returned for failure paths.

Covers (per Plan 02-02 Task 2 behavior spec):
  1. profile_list calls hermes with ["profile", "list"] and parses output
  2. profile_create always passes --no-alias (Pitfall #7)
  3. profile_create with clone_from appends --clone-from <src>
  4-6. profile_create error classification: duplicate / invalid_name / reserved_name
  7. profile_delete passes -y for non-interactive removal
  8. profile_delete missing → hint=not_found
  9. profile_rename argument order
 10. profile_export builds -o flag and returns the output path
 11. profile_show returns parsed ProfileShow dataclass
 12-13. doctor returns True for exit 0, False otherwise
 14. timeout sentinel (returncode=-9) translates to HermesCliError(hint=timeout)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters import HermesCliAdapter, HermesCliError
from app.adapters.hostops import CliResult
from app.adapters.parsers import ProfileShow
from tests.adapters.fakes import InMemoryHostOps

FIX = Path(__file__).parent.parent / "fixtures" / "hermes-cli"


# ---------------------------------------------------------------------------
# profile_list
# ---------------------------------------------------------------------------


async def test_profile_list_calls_hermes_with_correct_args() -> None:
    host = InMemoryHostOps()
    host.queue_response(
        ["profile", "list"],
        CliResult(0, (FIX / "profile_list_2_profiles.txt").read_text(), ""),
    )
    adapter = HermesCliAdapter(host)

    result = await adapter.profile_list()

    assert host.calls[0][0] == ["profile", "list"]
    assert any(p.name == "default" for p in result)
    assert any(p.name == "test-research-probe" for p in result)


# ---------------------------------------------------------------------------
# profile_create
# ---------------------------------------------------------------------------


async def test_profile_create_passes_no_alias_flag() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "", ""))
    adapter = HermesCliAdapter(host)

    await adapter.profile_create("test-bot")

    assert host.calls[-1][0] == ["profile", "create", "--no-alias", "test-bot"]


async def test_profile_create_with_clone_from_appends_flag() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "", ""))
    adapter = HermesCliAdapter(host)

    await adapter.profile_create("new", clone_from="src")

    assert host.calls[-1][0] == [
        "profile",
        "create",
        "--no-alias",
        "new",
        "--clone-from",
        "src",
    ]


async def test_profile_create_dup_raises_with_hint_duplicate() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(1, (FIX / "profile_create_dup_error.txt").read_text(), ""))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.profile_create("test-research-probe")

    assert exc.value.hint == "duplicate"
    assert exc.value.returncode == 1


async def test_profile_create_invalid_name_raises_with_hint_invalid_name() -> None:
    host = InMemoryHostOps()
    host.set_default_response(
        CliResult(1, (FIX / "profile_create_invalid_name.txt").read_text(), "")
    )
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.profile_create("BAD NAME")

    assert exc.value.hint == "invalid_name"


async def test_profile_create_reserved_default_raises_with_hint_reserved_name() -> None:
    host = InMemoryHostOps()
    host.set_default_response(
        CliResult(1, (FIX / "profile_create_default_rejected.txt").read_text(), "")
    )
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.profile_create("default")

    assert exc.value.hint == "reserved_name"


# ---------------------------------------------------------------------------
# profile_delete
# ---------------------------------------------------------------------------


async def test_profile_delete_passes_yes_flag() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "", ""))
    adapter = HermesCliAdapter(host)

    await adapter.profile_delete("test-bot")

    assert host.calls[-1][0] == ["profile", "delete", "-y", "test-bot"]


async def test_profile_delete_missing_raises_not_found() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(1, "Error: Profile 'ghost' does not exist.", ""))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.profile_delete("ghost")

    assert exc.value.hint == "not_found"


# ---------------------------------------------------------------------------
# profile_rename / profile_export
# ---------------------------------------------------------------------------


async def test_profile_rename_args() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "", ""))
    adapter = HermesCliAdapter(host)

    await adapter.profile_rename("a", "b")

    assert host.calls[-1][0] == ["profile", "rename", "a", "b"]


async def test_profile_export_returns_archive_path() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "", ""))
    adapter = HermesCliAdapter(host)
    target = Path("/tmp/x.tar.gz")

    result = await adapter.profile_export("test-bot", output_path=target)

    assert host.calls[-1][0] == ["profile", "export", "test-bot", "-o", "/tmp/x.tar.gz"]
    assert result == target


# ---------------------------------------------------------------------------
# profile_show
# ---------------------------------------------------------------------------


async def test_profile_show_returns_parsed_dataclass() -> None:
    host = InMemoryHostOps()
    host.queue_response(
        ["profile", "show", "test-research-probe"],
        CliResult(0, (FIX / "profile_show_default.txt").read_text(), ""),
    )
    adapter = HermesCliAdapter(host)

    result = await adapter.profile_show("test-research-probe")

    assert isinstance(result, ProfileShow)
    assert result.name == "test-research-probe"
    assert result.skills == 77
    assert result.env_configured is False


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


async def test_doctor_returns_true_for_exit_zero() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "", ""))
    adapter = HermesCliAdapter(host)

    assert await adapter.doctor() is True


async def test_doctor_returns_false_for_nonzero() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(1, "diagnostic warning", ""))
    adapter = HermesCliAdapter(host)

    assert await adapter.doctor() is False


# ---------------------------------------------------------------------------
# timeout sentinel
# ---------------------------------------------------------------------------


async def test_timeout_in_hostops_raises_hermes_cli_error_negative_returncode() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(returncode=-9, stdout="", stderr="timeout"))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.profile_list()

    assert exc.value.hint == "timeout"
    assert exc.value.returncode == -9


# ---------------------------------------------------------------------------
# gateway_setup / gateway_start / gateway_status (Phase 3)
# ---------------------------------------------------------------------------


async def test_gateway_setup_calls_hermes_with_correct_args() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "ok", ""))
    adapter = HermesCliAdapter(host)

    await adapter.gateway_setup("my-bot")

    assert host.calls[-1][0] == ["-p", "my-bot", "gateway", "setup"]
    # 60s timeout (gateway setup is the slow path)
    assert host.calls[-1][1] == 60.0


async def test_gateway_setup_raises_with_hint_gateway_setup_fail_on_nonzero() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(1, "", "boom"))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.gateway_setup("my-bot")

    assert exc.value.hint == "gateway_setup_fail"
    assert exc.value.returncode == 1


async def test_gateway_start_calls_hermes_with_correct_args() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "started", ""))
    adapter = HermesCliAdapter(host)

    await adapter.gateway_start("my-bot")

    assert host.calls[-1][0] == ["-p", "my-bot", "gateway", "start"]
    assert host.calls[-1][1] == 30.0


async def test_gateway_start_raises_with_hint_gateway_start_fail_on_nonzero() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(1, "fail", ""))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.gateway_start("my-bot")

    assert exc.value.hint == "gateway_start_fail"


async def test_gateway_status_returns_running_when_stdout_says_running() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "Gateway is RUNNING (pid 123)", ""))
    adapter = HermesCliAdapter(host)

    assert await adapter.gateway_status("my-bot") == "running"


async def test_gateway_status_returns_stopped_when_stdout_does_not_mention_running() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(0, "Gateway is stopped", ""))
    adapter = HermesCliAdapter(host)

    assert await adapter.gateway_status("my-bot") == "stopped"


async def test_gateway_setup_propagates_timeout_sentinel() -> None:
    host = InMemoryHostOps()
    host.set_default_response(CliResult(returncode=-9, stdout="", stderr="timeout"))
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.gateway_setup("my-bot")

    assert exc.value.hint == "timeout"


# ---------------------------------------------------------------------------
# check_lark_oapi / install_lark_oapi (Phase 3)
# ---------------------------------------------------------------------------


async def test_check_lark_oapi_calls_pip_show_via_run_command() -> None:
    host = InMemoryHostOps()
    host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(0, "Name: lark-oapi\nVersion: 1.5.5\n", ""),
    )
    adapter = HermesCliAdapter(host)

    installed, version = await adapter.check_lark_oapi()

    assert installed is True
    assert version == "1.5.5"
    assert host.command_calls[-1][0] == ["python3", "-m", "pip", "show", "lark-oapi"]
    assert host.command_calls[-1][1] == 15.0


async def test_check_lark_oapi_returns_false_when_pip_show_exits_nonzero() -> None:
    host = InMemoryHostOps()
    host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(1, "", "WARNING: Package not found"),
    )
    host.queue_command_response(
        [
            str(Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"),
            "-c",
            ("import importlib.metadata as m; import lark_oapi; print(m.version('lark-oapi'))"),
        ],
        CliResult(1, "", "ModuleNotFoundError"),
    )
    adapter = HermesCliAdapter(host)

    installed, version = await adapter.check_lark_oapi()

    assert installed is False
    assert version is None


async def test_check_lark_oapi_falls_back_to_hermes_runtime_import() -> None:
    host = InMemoryHostOps()
    host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(1, "", "WARNING: Package not found"),
    )
    host.queue_command_response(
        [
            str(Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"),
            "-c",
            ("import importlib.metadata as m; import lark_oapi; print(m.version('lark-oapi'))"),
        ],
        CliResult(0, "1.5.5\n", ""),
    )
    adapter = HermesCliAdapter(host)

    installed, version = await adapter.check_lark_oapi()

    assert installed is True
    assert version == "1.5.5"


async def test_install_lark_oapi_invokes_pip_install_with_pinned_version() -> None:
    host = InMemoryHostOps()
    host.queue_command_response(
        ["python3", "-m", "pip", "install", "lark-oapi==1.5.5"],
        CliResult(0, "Successfully installed lark-oapi-1.5.5\n", ""),
    )
    adapter = HermesCliAdapter(host)

    await adapter.install_lark_oapi()

    assert host.command_calls[-1][0] == ["python3", "-m", "pip", "install", "lark-oapi==1.5.5"]
    assert host.command_calls[-1][1] == 120.0


async def test_install_lark_oapi_raises_lark_oapi_missing_on_failure() -> None:
    host = InMemoryHostOps()
    host.queue_command_response(
        ["python3", "-m", "pip", "install", "lark-oapi==1.5.5"],
        CliResult(1, "", "ERROR: Could not find a version"),
    )
    adapter = HermesCliAdapter(host)

    with pytest.raises(HermesCliError) as exc:
        await adapter.install_lark_oapi()

    assert exc.value.hint == "lark_oapi_missing"
