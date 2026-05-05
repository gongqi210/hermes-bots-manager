"""Bot status decider — pure function. BOT-03 matrix.

Inputs are filesystem + process facts (no I/O here — caller gathers them).
Output: ``(BotStatus, why_string)``. The ``why`` string is the user-visible
"原因一行" shown in the Bot list card hover-tip.

The cmdline-match check is OPTIONAL in Phase 2 because v0.8 Hermes shares one
``gateway.pid`` across profiles (Pitfall #1: per-profile gateway lifecycle does
not exist yet). Phase 4 — once per-profile gateway lands — flips
``cmdline_check_enabled=True`` so we can detect "running but for the wrong
profile" cases.
"""

from __future__ import annotations

from enum import StrEnum


class BotStatus(StrEnum):
    """4-color dot per BOT-03. The string value is the API/JSON wire format."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    GREY = "grey"


def decide_bot_status(
    *,
    profile_exists: bool,
    env_exists: bool,
    gateway_state: str | None,  # 'running' | 'starting' | 'stopped' | 'error' | None
    pid_alive: bool,
    cmdline_matches: bool,
    cmdline_check_enabled: bool = False,  # Phase 4 flips to True
    error_reason: str | None = None,  # Phase 4 (D-18): stderr/hint snippet from CLI failures
) -> tuple[BotStatus, str]:
    """Pure decision function — see BOT-03 matrix in 02-RESEARCH.md.

    All flag inputs are pre-computed by ProfileFsAdapter +
    HostOps.get_process_info upstream. The function intentionally does no I/O —
    keeps the truth table table-driven and trivially testable.

    Decision order matters: profile_exists is the outermost gate (no profile →
    nothing else applies); .env presence gates the "configured" check; the
    gateway-state branches handle the running/starting/stopped/error cases.

    Phase 4 changes (D-17 / D-18):
      * ``cmdline_check_enabled=True`` + ``cmdline_matches=False`` → GREY (was RED in
        Phase 2). The user is not at fault — it's the v0.8 singleton-gateway
        constraint (Pitfall #1, lifted in M3).
      * ``error_reason`` injects the original CLI hint / stderr snippet (truncated
        to 100 chars) into the user-visible why string for ``gateway_state="error"``.
      * "PID 文件残留" wording made actionable: prompts user to click Restart.
    """
    if not profile_exists:
        return BotStatus.GREY, "Profile 不存在"
    if not env_exists:
        return BotStatus.GREY, "未配置 .env（请走完飞书接入向导）"  # noqa: RUF001
    if gateway_state is None:
        return BotStatus.GREY, "Gateway 未启动"
    if gateway_state == "stopped":
        return BotStatus.GREY, "已停止"
    if gateway_state == "error":
        if error_reason:
            snippet = error_reason[:100]
            return BotStatus.RED, f"Gateway 异常：{snippet}"  # noqa: RUF001
        return BotStatus.RED, "Gateway 异常（详见日志）"  # noqa: RUF001
    if gateway_state == "starting":
        if pid_alive:
            return BotStatus.YELLOW, "启动中"
        return BotStatus.RED, "启动中但进程不在"
    if gateway_state == "running":
        if not pid_alive:
            return BotStatus.RED, "Gateway 进程不存在（PID 文件残留，请点击 Restart 清理）"  # noqa: RUF001
        if cmdline_check_enabled and not cmdline_matches:
            # D-17: singleton-gateway constraint (Pitfall #1) — NOT this Bot's
            # fault, so render as GREY (informational), not RED (error).
            return BotStatus.GREY, "其他 Bot 正在使用 Gateway（Hermes 单例约束，M3 解除）"  # noqa: RUF001
        return BotStatus.GREEN, "运行中"
    # Unknown state — be conservative.
    return BotStatus.GREY, f"未知状态: {gateway_state}"
