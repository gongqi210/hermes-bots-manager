"""Gateway control router integration tests.

Covers BotService.compute_gateway_status (C1-C5) and the per-Bot REST flow
(G1-G7) with InMemoryHostOps + scripted hermes responses.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import (
    CliResult,
    HermesCliAdapter,
    ProfileFsAdapter,
)
from app.adapters.hostops import ProcessInfo
from app.models.bot import Bot
from app.services.bot import BotService
from app.services.gateway.supervisor import SupervisorRegistry
from tests.adapters.fakes import InMemoryHostOps

HERMES_HOME = Path("/h")


# ----------------------------------------------------------------------
# Fixtures.
# ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_host() -> InMemoryHostOps:
    return InMemoryHostOps()


@pytest_asyncio.fixture
async def app(engine: Any, fake_host: InMemoryHostOps) -> AsyncIterator[FastAPI]:
    """FastAPI app with Phase 4 deps overridden + a manually-wired app.state.

    We do NOT enter the real lifespan in tests (it would spawn a LogTailer
    against the host's ~/.hermes/logs/gateway.log). Instead we manually set
    ``app.state.{cli,host,fs,supervisor_registry,write_pairing}`` to point
    at the InMemoryHostOps fake so handlers can compose them without I/O.
    """
    from app.main import create_app

    a = create_app()

    cli = HermesCliAdapter(fake_host)
    fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
    registry = SupervisorRegistry()

    async def _no_op_writer(*_args: Any, **_kw: Any) -> None:
        return None

    a.state.cli = cli
    a.state.host = fake_host
    a.state.fs = fs
    a.state.supervisor_registry = registry
    a.state.write_pairing = _no_op_writer

    yield a


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _bootstrap_owner(client: AsyncClient) -> str:
    r = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "ownerpw9"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["tokens"]["access_token"])


def _seed_profile(host: InMemoryHostOps, name: str, *, with_env: bool = True) -> None:
    host.fs[HERMES_HOME / "profiles" / name / "config.yaml"] = "version: 1\n"
    if with_env:
        host.fs[HERMES_HOME / "profiles" / name / ".env"] = (
            "FEISHU_APP_ID=cli_x\nFEISHU_APP_SECRET=secret\n"
        )


def _seed_pid_file(host: InMemoryHostOps, *, pid: int = 4242, alive: bool = True) -> None:
    host.fs[HERMES_HOME / "gateway.pid"] = json.dumps(
        {"pid": pid, "kind": "gateway", "argv": ["hermes", "gateway"], "start_time": None}
    )
    host.process_table[pid] = ProcessInfo(
        pid=pid, cmdline=["hermes", "gateway"], environ={}, is_alive=alive
    )
    if alive:
        host.alive_pids.add(pid)


# ----------------------------------------------------------------------
# C1-C5 — BotService.compute_gateway_status (unit-ish, exercised via session).
# ----------------------------------------------------------------------


async def test_c1_running_when_pid_alive_and_active_profile_matches(
    fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    _seed_profile(fake_host, "foo")
    _seed_pid_file(fake_host, pid=4242, alive=True)
    fake_host.active_profile = "foo"
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    cli = HermesCliAdapter(fake_host)
    fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
    svc = BotService(session=session, cli=cli, fs=fs, archive_dir=Path("/h/archives"))
    out = await svc.compute_gateway_status("foo", host=fake_host, fs=fs, cli=cli)
    assert out.state == "running"
    assert out.is_active_profile is True
    assert out.pid == 4242
    assert out.why == "运行中"


async def test_c1b_running_when_named_profile_has_scoped_pid_file(
    fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    _seed_profile(fake_host, "foo")
    fake_host.fs[HERMES_HOME / "profiles" / "foo" / "gateway.pid"] = json.dumps(
        {"pid": 4243, "kind": "gateway", "argv": ["hermes", "--profile", "foo"], "start_time": None}
    )
    fake_host.process_table[4243] = ProcessInfo(
        pid=4243,
        cmdline=["hermes", "--profile", "foo", "gateway", "run"],
        environ={},
        is_alive=True,
    )
    fake_host.alive_pids.add(4243)
    fake_host.active_profile = "default"
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    cli = HermesCliAdapter(fake_host)
    fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
    svc = BotService(session=session, cli=cli, fs=fs, archive_dir=Path("/h/archives"))
    out = await svc.compute_gateway_status("foo", host=fake_host, fs=fs, cli=cli)
    assert out.state == "running"
    assert out.active_profile == "foo"
    assert out.is_active_profile is True
    assert out.pid == 4243


async def test_c2_error_when_pid_file_residual_process_gone(
    fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    _seed_profile(fake_host, "foo")
    _seed_pid_file(fake_host, pid=4242, alive=False)
    fake_host.active_profile = None
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    cli = HermesCliAdapter(fake_host)
    fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
    svc = BotService(session=session, cli=cli, fs=fs, archive_dir=Path("/h/archives"))
    out = await svc.compute_gateway_status("foo", host=fake_host, fs=fs, cli=cli)
    assert out.state == "error"
    assert "PID 文件残留" in out.why


async def test_c3_stopped_when_singleton_gateway_belongs_to_other_profile(
    fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    _seed_profile(fake_host, "foo")
    _seed_profile(fake_host, "bar")
    _seed_pid_file(fake_host, pid=4242, alive=True)
    fake_host.active_profile = "bar"
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    cli = HermesCliAdapter(fake_host)
    fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
    svc = BotService(session=session, cli=cli, fs=fs, archive_dir=Path("/h/archives"))
    out = await svc.compute_gateway_status("foo", host=fake_host, fs=fs, cli=cli)
    assert out.state == "stopped"
    assert out.is_active_profile is False
    assert "Hermes 单例约束" in out.why


async def test_c4_unconfigured_when_profile_missing(
    fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    cli = HermesCliAdapter(fake_host)
    fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
    svc = BotService(session=session, cli=cli, fs=fs, archive_dir=Path("/h/archives"))
    out = await svc.compute_gateway_status("ghost", host=fake_host, fs=fs, cli=cli)
    assert out.state == "unconfigured"
    assert "Profile 不存在" in out.why


async def test_c5_state_change_updates_cache_and_timestamp(
    fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    _seed_profile(fake_host, "foo")
    _seed_pid_file(fake_host, pid=4242, alive=True)
    fake_host.active_profile = "foo"
    bot = Bot(name="foo", tags=[])
    session.add(bot)
    await session.commit()

    cli = HermesCliAdapter(fake_host)
    fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
    svc = BotService(session=session, cli=cli, fs=fs, archive_dir=Path("/h/archives"))
    assert bot.gateway_state_cache == "unconfigured"
    out = await svc.compute_gateway_status("foo", host=fake_host, fs=fs, cli=cli)
    assert out.state == "running"
    session.expire_all()
    refreshed = (await session.execute(select(Bot).where(Bot.name == "foo"))).scalar_one()
    assert refreshed.gateway_state_cache == "running"
    assert refreshed.gateway_state_changed_at is not None


# ----------------------------------------------------------------------
# G1-G7 — Gateway router integration.
# ----------------------------------------------------------------------


async def test_g1_post_start_runs_cli_and_returns_running_state(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    _seed_profile(fake_host, "foo")
    fake_host.active_profile = "foo"
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    # gateway_start "succeeds" — and we drop the pid file at the same time
    # so the next poll sees state=running.
    def _cli_response(*_args: Any, **_kw: Any) -> CliResult:
        _seed_pid_file(fake_host, pid=5500, alive=True)
        return CliResult(0, "ok\n", "")

    fake_host.queue_response(["-p", "foo", "gateway", "start"], _cli_response())  # type: ignore[arg-type]

    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/bots/foo/gateway/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bot_name"] == "foo"
    assert body["action"] == "start"
    assert body["new_state"] == "running"
    assert isinstance(body["recent_log_tail"], list)


async def test_g2_post_stop_calls_hermes_stop(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    _seed_profile(fake_host, "foo")
    fake_host.active_profile = "foo"
    session.add(Bot(name="foo", tags=[]))
    await session.commit()
    fake_host.queue_response(["-p", "foo", "gateway", "stop"], CliResult(0, "ok\n", ""))

    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/bots/foo/gateway/stop",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert any(call[0] == ["-p", "foo", "gateway", "stop"] for call in fake_host.calls)


async def test_g3_post_restart_calls_hermes_restart(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    _seed_profile(fake_host, "foo")
    fake_host.active_profile = "foo"
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    def _restart_side_effect() -> CliResult:
        _seed_pid_file(fake_host, pid=6000, alive=True)
        return CliResult(0, "ok\n", "")

    fake_host.queue_response(["-p", "foo", "gateway", "restart"], _restart_side_effect())

    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/bots/foo/gateway/restart",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "restart"
    assert any(call[0] == ["-p", "foo", "gateway", "restart"] for call in fake_host.calls)


async def test_g4_concurrent_starts_serialize_via_per_profile_lock(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    """Two simultaneous /start calls do not race; the lock_for(bot) ensures
    they run sequentially."""
    _seed_profile(fake_host, "foo")
    fake_host.active_profile = "foo"
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    # Pre-seed pid so the polling window completes fast for both calls.
    _seed_pid_file(fake_host, pid=7000, alive=True)
    fake_host.set_default_response(CliResult(0, "ok\n", ""))

    token = await _bootstrap_owner(client)
    headers = {"Authorization": f"Bearer {token}"}
    r1, r2 = await asyncio.gather(
        client.post("/api/v1/bots/foo/gateway/start", headers=headers),
        client.post("/api/v1/bots/foo/gateway/start", headers=headers),
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    starts = [c for c in fake_host.calls if c[0] == ["-p", "foo", "gateway", "start"]]
    # Two calls expected — the lock serializes but does not coalesce.
    assert len(starts) == 2


async def test_g5_lock_acquisition_timeout_returns_503(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession, app: FastAPI
) -> None:
    """If the per-profile lock can't be acquired within LOCK_TIMEOUT_SEC the
    handler returns 503 with a 中文 message. We monkeypatch the timeout to a
    small value to keep the test fast."""
    import app.api.v1.gateway as gw_mod

    _seed_profile(fake_host, "foo")
    fake_host.active_profile = "foo"
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    registry: SupervisorRegistry = app.state.supervisor_registry
    lock = registry.lock_for("foo")
    await lock.acquire()  # hold it
    try:
        original = gw_mod.LOCK_TIMEOUT_SEC
        gw_mod.LOCK_TIMEOUT_SEC = 0  # type: ignore[misc]
        token = await _bootstrap_owner(client)
        try:
            r = await client.post(
                "/api/v1/bots/foo/gateway/start",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 503, r.text
            assert "Gateway 操作繁忙" in r.json()["detail"]
        finally:
            gw_mod.LOCK_TIMEOUT_SEC = original  # type: ignore[misc]
    finally:
        lock.release()


async def test_g6_get_status_returns_unconfigured_for_missing_profile(
    client: AsyncClient,
) -> None:
    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/ghost/gateway/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "unconfigured"


async def test_g7_action_response_contains_recent_log_tail(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Build a real on-disk gateway.log so the StreamingResponse can read it."""
    _seed_profile(fake_host, "foo")
    fake_host.active_profile = "foo"
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    log_path = tmp_path / "logs" / "gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("line one\nline two\nline three\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.adapters.hermes_cli.HermesCliAdapter.gateway_log_path",
        lambda self, profile=None: log_path,
    )

    _seed_pid_file(fake_host, pid=8000, alive=True)
    fake_host.queue_response(["-p", "foo", "gateway", "start"], CliResult(0, "ok\n", ""))

    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/bots/foo/gateway/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recent_log_tail"][:3] == ["line one", "line two", "line three"]


async def test_g_rbac_viewer_cannot_start(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    _seed_profile(fake_host, "foo")
    session.add(Bot(name="foo", tags=[]))
    await session.commit()
    owner_token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/auth/users",
        json={"username": "viewer1", "password": "viewerpw9", "role": "Viewer"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 201
    lr = await client.post(
        "/api/v1/auth/login", json={"username": "viewer1", "password": "viewerpw9"}
    )
    viewer_token = lr.json()["tokens"]["access_token"]
    r = await client.post(
        "/api/v1/bots/foo/gateway/start",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403, r.text
