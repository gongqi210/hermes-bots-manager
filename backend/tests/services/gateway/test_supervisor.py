"""Unit tests for GatewaySupervisor + SupervisorRegistry + lifespan integration.

Phase 4 GATEWAY-03 / GATEWAY-04 / GATEWAY-10 / D-01 / D-03.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.adapters.profile_fs import ProfileFsAdapter
from app.models.bot import Bot
from app.models.pairing import Pairing, PairingStatus
from app.services.gateway.broadcast_hub import BroadcastHub
from app.services.gateway.pairing_extractor import PairingCandidate
from app.services.gateway.pairing_writer import make_pairing_writer
from app.services.gateway.supervisor import GatewaySupervisor, SupervisorRegistry
from tests.adapters.fakes import InMemoryHostOps

HERMES_HOME = Path("/h")


# ---------- helpers ----------------------------------------------------------


def _make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def _seed_bot(session: AsyncSession, name: str = "foo") -> Bot:
    bot = Bot(name=name)
    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    return bot


async def _noop_write_pairing(_candidate: PairingCandidate) -> None:
    return None


# ---------- Supervisor unit tests (SU1-SU5) ----------------------------------


async def test_su1_processes_line_for_owned_profile() -> None:
    """SU1 — line for our profile → hub.publish + extract_pairing called."""
    host = InMemoryHostOps()
    host.active_profile = "foo"
    hub = BroadcastHub()
    sub = hub.subscribe(keywords=[], level_min=None)
    seen: list[PairingCandidate] = []

    async def write(c: PairingCandidate) -> None:
        seen.append(c)

    sup = GatewaySupervisor(bot_name="foo", hub=hub, write_pairing=write, host=host)
    line = "pairing code: ABCD1234"
    sup.deliver(line)
    sup.start()
    # Drain inbox.
    received = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert received == line
    # write_pairing should be invoked at least once with the right code.
    for _ in range(20):
        if seen:
            break
        await asyncio.sleep(0.02)
    assert seen and seen[0].code == "ABCD1234"
    await sup.shutdown()


async def test_su2_skips_line_when_profile_does_not_match() -> None:
    """SU2 — line belongs to a different profile → no publish + no extract."""
    host = InMemoryHostOps()
    host.active_profile = "other-bot"
    hub = BroadcastHub()
    sub = hub.subscribe(keywords=[], level_min=None)
    seen: list[PairingCandidate] = []

    async def write(c: PairingCandidate) -> None:
        seen.append(c)

    sup = GatewaySupervisor(bot_name="foo", hub=hub, write_pairing=write, host=host)
    sup.deliver("pairing code: ZZZZ9999")
    sup.start()
    # Give the loop time to process.
    await asyncio.sleep(0.05)
    assert sub.queue.empty()
    assert seen == []
    await sup.shutdown()


async def test_su3_pairing_extraction_calls_write_pairing() -> None:
    """SU3 — when extract_pairing returns a candidate, write_pairing is awaited."""
    host = InMemoryHostOps()
    host.active_profile = "foo"
    hub = BroadcastHub()
    seen: list[PairingCandidate] = []

    async def write(c: PairingCandidate) -> None:
        seen.append(c)

    sup = GatewaySupervisor(bot_name="foo", hub=hub, write_pairing=write, host=host)
    await sup.process_line("pairing code: ZZZ1111")
    assert len(seen) == 1
    assert seen[0].code == "ZZZ1111"
    assert seen[0].bot_name == "foo"


async def test_su4_shutdown_completes_within_timeout() -> None:
    """SU4 — shutdown cancels the run task within 5 seconds."""
    host = InMemoryHostOps()
    host.active_profile = "foo"
    hub = BroadcastHub()
    sup = GatewaySupervisor(bot_name="foo", hub=hub, write_pairing=_noop_write_pairing, host=host)
    sup.start()
    # Let it park on inbox.get().
    await asyncio.sleep(0.02)
    start = asyncio.get_event_loop().time()
    await sup.shutdown()
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 5.0


async def test_headless_pairing_capture(engine: AsyncEngine, session: AsyncSession) -> None:
    """SU5 / MAJOR 6 — pairing capture works with ZERO BroadcastHub subscribers.

    Proves GATEWAY-04 + GATEWAY-10: a Bot whose UI was never opened still
    gets pairings recorded in the DB.
    """
    await _seed_bot(session)
    host = InMemoryHostOps()
    host.active_profile = "foo"
    hub = BroadcastHub()  # ZERO subscribers — never call hub.subscribe().
    write = make_pairing_writer(_make_sessionmaker(engine))

    sup = GatewaySupervisor(bot_name="foo", hub=hub, write_pairing=write, host=host)
    sup.start()
    sup.deliver("2026-05-04 INFO pairing code: HEADL3SS")

    # Poll DB up to 1s for the row to land.
    deadline = asyncio.get_event_loop().time() + 1.0
    rows: list[Pairing] = []
    while asyncio.get_event_loop().time() < deadline:
        rows = list((await session.execute(select(Pairing))).scalars().all())
        if rows:
            break
        await asyncio.sleep(0.02)
    assert len(rows) == 1, "headless pairing capture must persist exactly one row"
    row = rows[0]
    assert row.status == PairingStatus.PENDING.value
    assert row.code_last4 == "L3SS"
    delta_minutes = (row.expires_at - row.intercepted_at).total_seconds() / 60
    assert abs(delta_minutes - 10) < 0.01
    # And, critically, the hub had zero subscribers for the whole flow.
    assert hub.subscriber_count == 0

    await sup.shutdown()


# ---------- SupervisorRegistry unit tests (SR1-SR4) --------------------------


async def test_sr1_start_all_creates_supervisor_per_env_configured_bot(
    tmp_path: Path,
) -> None:
    """SR1 — start_all walks fs.list_profiles and skips profiles without .env."""
    host = InMemoryHostOps()
    fs = ProfileFsAdapter(host=host, hermes_home=HERMES_HOME)
    # Two profiles in fs.list_profiles: foo (.env present), bar (no .env).
    host.fs[HERMES_HOME / "profiles" / "foo" / ".env"] = "FEISHU_APP_ID=x\n"
    host.fs[HERMES_HOME / "profiles" / "bar" / "config.yaml"] = "version: 1\n"
    log_path = tmp_path / "gateway.log"
    log_path.touch()

    registry = SupervisorRegistry()
    await registry.start_all(fs=fs, host=host, log_path=log_path, write_pairing=_noop_write_pairing)
    try:
        assert registry.get("foo") is not None
        assert registry.get("bar") is None
        assert registry.all_bots() == ["foo"]
    finally:
        await registry.shutdown_all()


async def test_sr2_lock_for_returns_same_lock_per_bot() -> None:
    """SR2 — per-profile asyncio.Lock is identity-stable across calls (GATEWAY-03)."""
    registry = SupervisorRegistry()
    a = registry.lock_for("foo")
    b = registry.lock_for("foo")
    assert a is b
    other = registry.lock_for("bar")
    assert other is not a


async def test_sr3_shutdown_does_not_call_hermes_cli(tmp_path: Path) -> None:
    """SR3 — shutdown_all NEVER invokes hermes CLI (D-03 — don't kill Hermes Gateway)."""
    host = InMemoryHostOps()
    fs = ProfileFsAdapter(host=host, hermes_home=HERMES_HOME)
    host.fs[HERMES_HOME / "profiles" / "foo" / ".env"] = "FEISHU_APP_ID=x\n"
    log_path = tmp_path / "gateway.log"
    log_path.touch()

    registry = SupervisorRegistry()
    await registry.start_all(fs=fs, host=host, log_path=log_path, write_pairing=_noop_write_pairing)
    pre_calls = len(host.calls)
    await registry.shutdown_all()
    post_calls = len(host.calls)
    # No hermes invocations during shutdown.
    assert post_calls == pre_calls


async def test_sr4_get_returns_supervisor_or_none(tmp_path: Path) -> None:
    """SR4 — get() is the registry lookup contract for Wave 3 REST handlers."""
    host = InMemoryHostOps()
    fs = ProfileFsAdapter(host=host, hermes_home=HERMES_HOME)
    host.fs[HERMES_HOME / "profiles" / "foo" / ".env"] = "FEISHU_APP_ID=x\n"
    log_path = tmp_path / "gateway.log"
    log_path.touch()

    registry = SupervisorRegistry()
    await registry.start_all(fs=fs, host=host, log_path=log_path, write_pairing=_noop_write_pairing)
    try:
        sup = registry.get("foo")
        assert isinstance(sup, GatewaySupervisor)
        assert registry.get("ghost") is None
    finally:
        await registry.shutdown_all()


async def test_registry_dispatcher_fans_lines_out_to_active_supervisor(
    tmp_path: Path,
) -> None:
    """End-to-end: writing to gateway.log → dispatcher → active supervisor → hub."""
    host = InMemoryHostOps()
    host.active_profile = "foo"
    fs = ProfileFsAdapter(host=host, hermes_home=HERMES_HOME)
    host.fs[HERMES_HOME / "profiles" / "foo" / ".env"] = "FEISHU_APP_ID=x\n"

    log_path = tmp_path / "gateway.log"
    log_path.write_text("")

    registry = SupervisorRegistry()
    await registry.start_all(fs=fs, host=host, log_path=log_path, write_pairing=_noop_write_pairing)
    try:
        sup = registry.get("foo")
        assert sup is not None
        sub = sup.hub.subscribe(keywords=[], level_min=None)
        # Append a line; LogTailer polls every 250ms.
        with open(log_path, "a") as f:
            f.write("2026-05-04 INFO from foo profile\n")
        line = await asyncio.wait_for(sub.queue.get(), timeout=2.0)
        assert "from foo profile" in line
    finally:
        await registry.shutdown_all()


async def test_registry_add_bot_registers_supervisor_mid_lifespan(
    tmp_path: Path,
) -> None:
    """Wizard finish path: add_bot registers a Supervisor without re-running start_all."""
    host = InMemoryHostOps()
    fs = ProfileFsAdapter(host=host, hermes_home=HERMES_HOME)
    log_path = tmp_path / "gateway.log"
    log_path.touch()

    registry = SupervisorRegistry()
    await registry.start_all(fs=fs, host=host, log_path=log_path, write_pairing=_noop_write_pairing)
    try:
        assert registry.get("late") is None
        await registry.add_bot("late")
        assert registry.get("late") is not None
        # add_bot is idempotent.
        await registry.add_bot("late")
        assert registry.all_bots().count("late") == 1
    finally:
        await registry.shutdown_all()


async def test_supervisor_module_does_not_invoke_hermes_cli_or_kill_processes() -> None:
    """D-03 grep enforcement at module import time.

    The supervisor.py source MUST NOT contain string references to
    ``gateway_stop`` or ``run_hermes`` (lifespan shutdown is hands-off vs
    Hermes processes). This is the same check the plan acceptance criteria
    runs via grep, hard-wired into the test suite as a regression guard.
    """
    src = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "app"
        / "services"
        / "gateway"
        / "supervisor.py"
    )
    body = src.read_text(encoding="utf-8")
    forbidden = re.compile(r"\b(gateway_stop|run_hermes)\s*\(")
    assert forbidden.search(body) is None, (
        "supervisor.py must NOT call gateway_stop / run_hermes — D-03"
    )


# ---------- Lifespan integration tests (LS1-LS3) -----------------------------
#
# httpx.ASGITransport does NOT automatically trigger ASGI lifespan events, so
# we invoke ``app.main.lifespan`` directly as an async context manager. This
# is the same pattern Starlette's TestClient uses internally and avoids
# pulling in asgi-lifespan as a new dev dependency.


async def _build_lifespan_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FastAPI:
    """Build a FastAPI app with HERMES_HOME pointed at an empty temp dir.

    Avoids touching ``~/.hermes`` on the developer's machine — the lifespan
    will see an empty profiles list and create zero supervisors, but it MUST
    still wire up app.state and the TTL loop.
    """
    monkeypatch.setenv("HERMES_CONSOLE_HERMES_HOME", str(tmp_path / "hermes"))
    import app.config as c

    c._settings = None
    from app.main import create_app

    return create_app()


async def test_ls1_lifespan_wires_app_state(
    engine: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LS1 — startup wires registry / write_pairing / host / fs / cli on app.state."""
    fastapi_app = await _build_lifespan_app(monkeypatch, tmp_path)
    from app.main import lifespan

    async with lifespan(fastapi_app):
        assert isinstance(fastapi_app.state.supervisor_registry, SupervisorRegistry)
        assert callable(fastapi_app.state.write_pairing)
        assert fastapi_app.state.host is not None
        assert fastapi_app.state.fs is not None
        assert fastapi_app.state.cli is not None


async def test_ls2_lifespan_starts_and_shuts_down_registry(
    engine: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LS2 — startup calls registry.start_all; shutdown calls registry.shutdown_all.

    With an empty HERMES_HOME we observe zero Supervisors but verify the
    registry survives startup AND shutdown completes inside the bounded
    timeout (no hang).
    """
    fastapi_app = await _build_lifespan_app(monkeypatch, tmp_path)
    from app.main import lifespan

    async with lifespan(fastapi_app):
        registry = fastapi_app.state.supervisor_registry
        assert registry.all_bots() == []
    # If shutdown hung, the async with would not return; reaching here proves
    # the bounded shutdown path works.


async def test_ls3_pairing_ttl_task_is_running(
    engine: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LS3 — the pairing TTL cleanup task is registered during the lifespan body."""
    fastapi_app = await _build_lifespan_app(monkeypatch, tmp_path)
    from app.main import lifespan

    async with lifespan(fastapi_app):
        names = {t.get_name() for t in asyncio.all_tasks()}
        assert "PairingTTLCleanup" in names
