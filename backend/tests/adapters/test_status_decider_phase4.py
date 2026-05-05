"""Tests for Phase 4 status_decider extensions (Plan 04-03 Task 2).

Phase 4 changes (D-17 / D-18):
  S1: cmdline_check_enabled=True + cmdline_matches=False → GREY (singleton-gateway)
  S2: gateway_state="error" + error_reason → why-string includes the reason
  S3: gateway_state="running" + pid_alive=False → "Gateway 进程不存在 ... PID 文件残留 ..."
  S4: error_reason longer than 100 chars → truncated
  S5: backwards compat — Phase 2 cases unchanged when cmdline_check_enabled=False

Phase 2 truth-table coverage stays in test_status_decider.py.
"""

from __future__ import annotations

import pytest

from app.adapters.status_decider import BotStatus, decide_bot_status

# Sensible defaults that callers override per test (mirrors Phase 2 file).
DEFAULTS = {
    "profile_exists": True,
    "env_exists": True,
    "gateway_state": "running",
    "pid_alive": True,
    "cmdline_matches": True,
    "cmdline_check_enabled": False,
    "error_reason": None,
}


def _decide(**overrides: object) -> tuple[BotStatus, str]:
    args = {**DEFAULTS, **overrides}
    return decide_bot_status(**args)  # type: ignore[arg-type]


# ---- S1: singleton-gateway (D-17) ---------------------------------------------


def test_s1_singleton_gateway_returns_grey_with_chinese_explanation() -> None:
    """Phase 2 returned RED here; Phase 4 returns GREY with the singleton hint.

    The user is not at fault — another Bot legitimately owns the singleton
    gateway PID; flipping to GREY removes the false alarm. M3 lifts this.
    """
    color, why = _decide(cmdline_matches=False, cmdline_check_enabled=True)
    assert color == BotStatus.GREY
    assert why == "其他 Bot 正在使用 Gateway（Hermes 单例约束，M3 解除）"  # noqa: RUF001


# ---- S2: error_reason injects into gateway-error why string -------------------


def test_s2_error_reason_appears_in_why_string() -> None:
    color, why = _decide(gateway_state="error", error_reason="lark-oapi 未安装")
    assert color == BotStatus.RED
    assert "lark-oapi 未安装" in why


def test_s2_error_state_without_reason_uses_generic_message() -> None:
    """Backwards compat: error_reason=None falls back to the Phase 2 wording."""
    color, why = _decide(gateway_state="error")
    assert color == BotStatus.RED
    assert why == "Gateway 异常（详见日志）"  # noqa: RUF001


# ---- S3: PID-file residue language ------------------------------------------


def test_s3_pid_file_residue_renders_actionable_red() -> None:
    """Wording asks user to click Restart (the actionable next step)."""
    color, why = _decide(gateway_state="running", pid_alive=False)
    assert color == BotStatus.RED
    assert "PID 文件残留" in why
    assert "Restart" in why


# ---- S4: error_reason truncation --------------------------------------------


def test_s4_error_reason_longer_than_100_chars_is_truncated() -> None:
    """Bound the user-visible message; full text remains in audit log."""
    long_reason = "boom: " + "x" * 200
    color, why = _decide(gateway_state="error", error_reason=long_reason)
    assert color == BotStatus.RED
    # Truncated to 100 chars max; full untruncated string must NOT appear.
    truncated_payload = long_reason[:100]
    assert truncated_payload in why
    assert long_reason not in why


# ---- S5: backwards compat --------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected_color", "expected_why_substr"),
    [
        ({"profile_exists": False}, BotStatus.GREY, "Profile 不存在"),
        ({"env_exists": False}, BotStatus.GREY, "未配置 .env"),
        ({"gateway_state": None}, BotStatus.GREY, "Gateway 未启动"),
        ({"gateway_state": "stopped"}, BotStatus.GREY, "已停止"),
        ({"gateway_state": "starting", "pid_alive": True}, BotStatus.YELLOW, "启动中"),
        (
            {"gateway_state": "starting", "pid_alive": False},
            BotStatus.RED,
            "启动中但进程不在",
        ),
        # Phase 2 default — cmdline_check disabled → mismatch is benign.
        (
            {"cmdline_matches": False, "cmdline_check_enabled": False},
            BotStatus.GREEN,
            "运行中",
        ),
    ],
)
def test_s5_phase2_truth_table_unchanged(
    overrides: dict[str, object],
    expected_color: BotStatus,
    expected_why_substr: str,
) -> None:
    color, why = _decide(**overrides)
    assert color == expected_color
    assert expected_why_substr in why
