"""WizardService unit tests — 7-step SSE generator + idempotency.

Uses InMemoryHostOps for the CLI/fs side, real session for DB side.
Asserts on the SSE frames yielded by ``WizardService.run()`` — each test
parses ``data: {json}\\n\\n`` frames into dicts.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import (
    CliResult,
    HermesCliAdapter,
    ProcessInfo,
    ProfileFsAdapter,
)
from app.models.bot import Bot
from app.services.wizard import WizardService, WizardStepError
from tests.adapters.fakes import InMemoryHostOps

HERMES_HOME = Path("/h")


def _build_service(host: InMemoryHostOps, session: AsyncSession) -> WizardService:
    cli = HermesCliAdapter(host)
    fs = ProfileFsAdapter(host, hermes_home=HERMES_HOME)
    return WizardService(cli=cli, fs=fs, session=session)


def _parse_frames(frames: list[str]) -> list[dict]:
    """Parse SSE frames into dicts. Each frame is ``data: {json}\\n\\n``."""
    out: list[dict] = []
    for f in frames:
        assert f.startswith("data: ")
        assert f.endswith("\n\n")
        body = f[len("data: ") : -2]
        out.append(json.loads(body))
    return out


def _seed_bot(session: AsyncSession, name: str = "alpha") -> Bot:
    bot = Bot(name=name, feishu_app_id="cli_xxxx", tags=[])
    session.add(bot)
    return bot


def _setup_happy_path(host: InMemoryHostOps, name: str) -> None:
    """Queue all CLI responses for a successful 7-step run."""
    # Step 2: profile create
    host.queue_response(
        ["profile", "create", "--no-alias", name],
        CliResult(0, "ok", ""),
    )
    # Step 4: pip show lark-oapi → installed
    host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(0, "Version: 1.5.5\n", ""),
    )
    # Step 5: gateway setup
    host.queue_response(["-p", name, "gateway", "setup"], CliResult(0, "configured", ""))
    # Step 6: gateway start
    host.queue_response(["-p", name, "gateway", "start"], CliResult(0, "started", ""))
    # Step 7: doctor (default profile-aware variant)
    host.queue_response(["-p", name, "doctor"], CliResult(0, "OK", ""))


# ---------------------------------------------------------------------------
# Test 1: full happy path yields 7 step frames + final done event
# ---------------------------------------------------------------------------


async def test_wizard_run_yields_seven_step_frames_plus_done(session: AsyncSession) -> None:
    host = InMemoryHostOps()
    _seed_bot(session)
    await session.commit()
    _setup_happy_path(host, "alpha")
    svc = _build_service(host, session)

    frames = [
        f
        async for f in svc.run(
            "alpha",
            "cli_xxxxlonger",
            SecretStr("secret-value-12"),
            "feishu",
            "websocket",
            "mention",
        )
    ]
    events = _parse_frames(frames)

    # Step 1 emits running + success → 2 frames; same for steps 2..7 → 14 frames
    # plus final done → 15 frames total.
    step_succeeded = [e for e in events if e.get("status") == "success"]
    assert len(step_succeeded) == 7
    assert events[-1]["status"] == "done"
    assert events[-1]["step"] == 0
    # All 7 steps reached.
    steps_seen = {e["step"] for e in events if e["step"] > 0}
    assert steps_seen == {1, 2, 3, 4, 5, 6, 7}

    env_body = host.fs[HERMES_HOME / "profiles" / "alpha" / ".env"]
    assert "FEISHU_CONNECTION_MODE=websocket" in env_body
    assert "FEISHU_GROUP_POLICY=open" in env_body
    assert "FEISHU_REQUIRE_MENTION=true" in env_body
    assert "FEISHU_GROUP_STRATEGY" not in env_body


# ---------------------------------------------------------------------------
# Test 2: failure at step 2 stops execution, no later steps run
# ---------------------------------------------------------------------------


async def test_wizard_stops_on_step2_failure(session: AsyncSession) -> None:
    host = InMemoryHostOps()
    _seed_bot(session)
    await session.commit()
    # profile_create returns duplicate error.
    host.queue_response(
        ["profile", "create", "--no-alias", "alpha"],
        CliResult(1, "Profile 'alpha' already exists\n", ""),
    )
    svc = _build_service(host, session)

    frames = [
        f
        async for f in svc.run(
            "alpha",
            "cli_xxxxlonger",
            SecretStr("secret-value-12"),
            "feishu",
            "websocket",
            "mention",
        )
    ]
    events = _parse_frames(frames)

    # Step 1 success, step 2 error, no steps 3-7.
    statuses = {(e["step"], e["status"]) for e in events}
    assert (1, "success") in statuses
    assert (2, "error") in statuses
    assert not any(e["step"] in (3, 4, 5, 6, 7) for e in events)
    # No 'done' event emitted on error path.
    assert not any(e.get("status") == "done" for e in events)


# ---------------------------------------------------------------------------
# Test 3: idempotency — step 2 skipped when profile dir exists
# ---------------------------------------------------------------------------


async def test_wizard_skips_step2_when_profile_dir_already_exists(
    session: AsyncSession,
) -> None:
    host = InMemoryHostOps()
    _seed_bot(session)
    await session.commit()
    # Pretend profile dir exists already.
    host.fs[HERMES_HOME / "profiles" / "alpha" / "config.yaml"] = "version: 1\n"
    # Pretend .env already exists too (step 3 also skipped).
    host.fs[HERMES_HOME / "profiles" / "alpha" / ".env"] = "FEISHU_APP_ID=cli_x\n"
    # lark-oapi installed
    host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(0, "Version: 1.5.5\n", ""),
    )
    # gateway.yaml and pid file exist
    host.fs[HERMES_HOME / "profiles" / "alpha" / "gateway.yaml"] = "x: 1\n"
    host.fs[HERMES_HOME / "profiles" / "alpha" / "gateway.pid"] = "42\n"
    host.process_table[42] = ProcessInfo(pid=42, cmdline=["hermes"], environ={}, is_alive=True)
    # doctor still runs
    host.queue_response(["-p", "alpha", "doctor"], CliResult(0, "ok", ""))

    svc = _build_service(host, session)
    frames = [
        f
        async for f in svc.run(
            "alpha",
            "cli_xxxxlonger",
            SecretStr("secret-value-12"),
            "feishu",
            "websocket",
            "mention",
        )
    ]
    events = _parse_frames(frames)

    # Step 2 success WITHOUT a 'running' frame (skipped path emits only success).
    s2_frames = [e for e in events if e["step"] == 2]
    assert len(s2_frames) == 1
    assert s2_frames[0]["status"] == "success"
    assert "已完成" in s2_frames[0]["message"] or "跳过" in s2_frames[0]["message"]

    # Same for step 6.
    s6_frames = [e for e in events if e["step"] == 6]
    assert len(s6_frames) == 1
    assert s6_frames[0]["status"] == "success"


async def test_wizard_step5_does_not_call_interactive_gateway_setup_when_feishu_env_exists(
    session: AsyncSession,
) -> None:
    host = InMemoryHostOps()
    _seed_bot(session)
    await session.commit()
    host.fs[HERMES_HOME / "profiles" / "alpha" / "config.yaml"] = "version: 1\n"
    host.fs[HERMES_HOME / "profiles" / "alpha" / ".env"] = (
        "FEISHU_APP_ID=cli_xxxxlonger\nFEISHU_APP_SECRET=secret-value-12\n"
    )
    host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(0, "Version: 1.5.5\n", ""),
    )
    host.queue_response(["-p", "alpha", "gateway", "start"], CliResult(0, "started", ""))
    host.queue_response(["-p", "alpha", "doctor"], CliResult(0, "ok", ""))

    svc = _build_service(host, session)
    frames = [
        f
        async for f in svc.run(
            "alpha",
            "cli_xxxxlonger",
            SecretStr("secret-value-12"),
            "feishu",
            "websocket",
            "mention",
        )
    ]

    events = _parse_frames(frames)
    assert events[-1]["status"] == "done"
    assert ["-p", "alpha", "gateway", "setup"] not in [call[0] for call in host.calls]


# ---------------------------------------------------------------------------
# Test 4: lark-oapi missing → fix_hint='lark_oapi_missing' on step 4 error
# ---------------------------------------------------------------------------


async def test_wizard_step4_error_includes_lark_oapi_missing_hint(
    session: AsyncSession,
) -> None:
    host = InMemoryHostOps()
    _seed_bot(session)
    await session.commit()
    # profile_create succeeds
    host.queue_response(
        ["profile", "create", "--no-alias", "alpha"],
        CliResult(0, "ok", ""),
    )
    # Step 4: lark-oapi check returns False, then install fails.
    host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(1, "", ""),
    )
    host.queue_command_response(
        [
            str(Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"),
            "-c",
            ("import importlib.metadata as m; import lark_oapi; print(m.version('lark-oapi'))"),
        ],
        CliResult(1, "", "ModuleNotFoundError"),
    )
    host.queue_command_response(
        ["python3", "-m", "pip", "install", "lark-oapi==1.5.5"],
        CliResult(1, "", "ERROR: ..."),
    )
    svc = _build_service(host, session)

    frames = [
        f
        async for f in svc.run(
            "alpha",
            "cli_xxxxlonger",
            SecretStr("secret-value-12"),
            "feishu",
            "websocket",
            "mention",
        )
    ]
    events = _parse_frames(frames)

    s4_err = next(e for e in events if e["step"] == 4 and e["status"] == "error")
    assert s4_err["fix_hint"] == "lark_oapi_missing"


# ---------------------------------------------------------------------------
# Test 5: App Secret never appears in any SSE frame
# ---------------------------------------------------------------------------


async def test_wizard_secret_never_appears_in_sse_frames(session: AsyncSession) -> None:
    host = InMemoryHostOps()
    _seed_bot(session)
    await session.commit()
    secret_value = "super-secret-value-12345"
    # Cause failure at step 5 (gateway setup) so error frame fires
    host.queue_response(
        ["profile", "create", "--no-alias", "alpha"],
        CliResult(0, "ok", ""),
    )
    host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(0, "Version: 1.5.5\n", ""),
    )
    # Gateway setup fails with stderr that COULD contain a secret-shaped string
    # (we want to confirm it's stripped).
    host.queue_response(
        ["-p", "alpha", "gateway", "setup"],
        CliResult(1, "", f"failed reading {secret_value}"),
    )
    svc = _build_service(host, session)

    frames = [
        f
        async for f in svc.run(
            "alpha",
            "cli_xxxxlonger",
            SecretStr(secret_value),
            "feishu",
            "websocket",
            "mention",
        )
    ]
    # Cross-check: no frame contains the plaintext secret.
    for frame in frames:
        assert secret_value not in frame, f"plaintext secret leaked into SSE frame: {frame!r}"


# ---------------------------------------------------------------------------
# Test 9: terminal done event has step=0
# ---------------------------------------------------------------------------


async def test_wizard_emits_terminal_done_event_after_all_steps_succeed(
    session: AsyncSession,
) -> None:
    host = InMemoryHostOps()
    _seed_bot(session)
    await session.commit()
    _setup_happy_path(host, "alpha")
    svc = _build_service(host, session)

    frames = [
        f
        async for f in svc.run(
            "alpha",
            "cli_xxxxlonger",
            SecretStr("secret-value-12"),
            "feishu",
            "websocket",
            "mention",
        )
    ]
    events = _parse_frames(frames)
    last = events[-1]
    assert last["step"] == 0
    assert last["status"] == "done"


# ---------------------------------------------------------------------------
# Test: WizardStepError surfaces fix_hint cleanly
# ---------------------------------------------------------------------------


def test_wizard_step_error_carries_fix_hint() -> None:
    err = WizardStepError(4, "lark-oapi 安装失败", "pip failed", fix_hint="lark_oapi_missing")
    assert err.step == 4
    assert err.fix_hint == "lark_oapi_missing"
    assert "lark-oapi" in str(err)


# ---------------------------------------------------------------------------
# Test: Step 1 rejects malformed App ID
# ---------------------------------------------------------------------------


async def test_wizard_step1_rejects_short_app_id(session: AsyncSession) -> None:
    host = InMemoryHostOps()
    _seed_bot(session)
    await session.commit()
    svc = _build_service(host, session)

    frames = [
        f
        async for f in svc.run(
            "alpha",
            "cli",
            SecretStr("secret-value-12"),
            "feishu",
            "websocket",
            "mention",
        )
    ]
    events = _parse_frames(frames)
    s1_err = next(e for e in events if e["step"] == 1 and e["status"] == "error")
    assert s1_err["fix_hint"] == "feishu_auth_fail"
