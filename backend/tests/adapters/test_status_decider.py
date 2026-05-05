"""Unit tests for decide_bot_status — table-driven BOT-03 matrix coverage.

Plan 02-03 Task 2. Pure function, no fakes needed. One test per matrix row +
edge cases (cmdline check enabled vs disabled, all 4 enum values present).
"""

from __future__ import annotations

from app.adapters.status_decider import BotStatus, decide_bot_status

# Sensible defaults that callers override per test.
DEFAULTS = {
    "profile_exists": True,
    "env_exists": True,
    "gateway_state": "running",
    "pid_alive": True,
    "cmdline_matches": True,
    "cmdline_check_enabled": False,
}


def _decide(**overrides: object) -> tuple[BotStatus, str]:
    args = {**DEFAULTS, **overrides}
    return decide_bot_status(**args)  # type: ignore[arg-type]


# ---- BOT-03 matrix rows ------------------------------------------------------


def test_status_grey_when_profile_missing() -> None:
    """Row 1: no profile dir → grey 'Profile 不存在'."""
    color, why = _decide(profile_exists=False)
    assert color == BotStatus.GREY
    assert why == "Profile 不存在"


def test_status_grey_when_env_missing() -> None:
    """Row 2: profile exists, .env missing → grey 'unconfigured' onboarding hint."""
    color, why = _decide(env_exists=False)
    assert color == BotStatus.GREY
    assert "未配置 .env" in why


def test_status_grey_when_gateway_state_file_missing() -> None:
    """Row 3: .env present but no gateway_state.json → grey 'Gateway 未启动'."""
    color, why = _decide(gateway_state=None)
    assert color == BotStatus.GREY
    assert why == "Gateway 未启动"


def test_status_red_when_pid_file_orphaned() -> None:
    """Row 4: state=running but PID dead → red 'orphaned'."""
    color, why = _decide(gateway_state="running", pid_alive=False)
    assert color == BotStatus.RED
    assert "残留" in why


def test_status_grey_when_cmdline_mismatch_under_singleton_gateway() -> None:
    """Row 5 (Phase 4 D-17): state=running, PID alive, cmdline ≠ profile → grey.

    Was RED in Phase 2 — flipped to GREY in Phase 4 because the singleton-
    gateway constraint (Pitfall #1) means another Bot legitimately owns the
    process; this Bot is not at fault. Restored to per-profile gateways in M3.
    """
    color, why = _decide(
        gateway_state="running",
        pid_alive=True,
        cmdline_matches=False,
        cmdline_check_enabled=True,
    )
    assert color == BotStatus.GREY
    assert "Hermes 单例约束" in why


def test_status_green_when_running_and_cmdline_match() -> None:
    """Row 6: all-green inputs → green 'running'."""
    color, why = _decide(
        gateway_state="running",
        pid_alive=True,
        cmdline_matches=True,
        cmdline_check_enabled=True,
    )
    assert color == BotStatus.GREEN
    assert why == "运行中"


def test_status_green_when_cmdline_check_disabled() -> None:
    """Phase 2 default: cmdline_check_enabled=False → mismatch is ignored.

    Pitfall #1 — Hermes v0.8 shares one PID across profiles, so we can't trust
    the cmdline match until per-profile gateway lands (Phase 4).
    """
    color, why = _decide(
        gateway_state="running",
        pid_alive=True,
        cmdline_matches=False,  # would be red if check_enabled=True
        cmdline_check_enabled=False,
    )
    assert color == BotStatus.GREEN
    assert why == "运行中"


def test_status_yellow_when_starting() -> None:
    """Row 7: state=starting, PID alive → yellow 'starting'."""
    color, why = _decide(gateway_state="starting", pid_alive=True)
    assert color == BotStatus.YELLOW
    assert why == "启动中"


def test_status_red_when_starting_but_pid_dead() -> None:
    """Edge: state=starting but PID dead → red (degraded starting)."""
    color, why = _decide(gateway_state="starting", pid_alive=False)
    assert color == BotStatus.RED
    assert "启动中但进程不在" in why


def test_status_grey_when_stopped() -> None:
    """Row 8: state=stopped → grey 'stopped'."""
    color, why = _decide(gateway_state="stopped")
    assert color == BotStatus.GREY
    assert why == "已停止"


def test_status_red_when_error_state() -> None:
    """Row 9: state=error → red 'gateway error'."""
    color, why = _decide(gateway_state="error")
    assert color == BotStatus.RED
    assert "Gateway 异常" in why


def test_status_grey_for_unknown_state() -> None:
    """Defensive: unknown gateway_state value → grey with the literal value."""
    color, why = _decide(gateway_state="warp-drive")
    assert color == BotStatus.GREY
    assert "warp-drive" in why


# ---- Enum invariants ---------------------------------------------------------


def test_botstatus_enum_has_four_values() -> None:
    """BOT-03 specifies exactly 4 colors. No fifth — yellow IS the warning state."""
    values = {s.value for s in BotStatus}
    assert values == {"green", "yellow", "red", "grey"}


def test_decide_bot_status_returns_typed_botstatus() -> None:
    """Return type contract: (BotStatus, str) tuple."""
    color, why = _decide()
    assert isinstance(color, BotStatus)
    assert isinstance(why, str)
