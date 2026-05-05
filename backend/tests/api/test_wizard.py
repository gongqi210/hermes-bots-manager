"""Wizard API endpoint integration tests.

Covers:
  - GET /api/v1/bots/check-app-id (FEISHU-05)
  - PATCH /api/v1/bots/{name}/secret (FEISHU-04, RBAC)
  - GET /api/v1/bots/{name}/wizard/run (FEISHU-02 + secret-from-DB design)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import HermesCliAdapter, ProfileFsAdapter
from app.auth.crypto import encrypt_str
from app.models.bot import Bot
from app.services.bot import BotService
from app.services.wizard import WizardService
from tests.adapters.fakes import InMemoryHostOps

HERMES_HOME = Path("/h")


@pytest_asyncio.fixture
async def fake_host() -> InMemoryHostOps:
    return InMemoryHostOps()


@pytest_asyncio.fixture
async def app(engine: Any, fake_host: InMemoryHostOps) -> AsyncIterator[FastAPI]:
    """FastAPI app with both bot + wizard service overrides using InMemoryHostOps."""
    from app.api.v1.bots import get_bot_service
    from app.api.v1.wizard import _get_bot_service as _wizard_bot_svc
    from app.api.v1.wizard import _get_wizard_service
    from app.db.session import get_sessionmaker
    from app.main import create_app

    a = create_app()

    async def _bot_override() -> AsyncIterator[BotService]:
        cli = HermesCliAdapter(fake_host)
        fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
        maker: async_sessionmaker[AsyncSession] = get_sessionmaker()
        async with maker() as session:
            try:
                yield BotService(session=session, cli=cli, fs=fs, archive_dir=Path("/h/archives"))
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _wizard_override() -> AsyncIterator[WizardService]:
        cli = HermesCliAdapter(fake_host)
        fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
        maker: async_sessionmaker[AsyncSession] = get_sessionmaker()
        async with maker() as session:
            try:
                yield WizardService(cli=cli, fs=fs, session=session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    a.dependency_overrides[get_bot_service] = _bot_override
    a.dependency_overrides[_wizard_bot_svc] = _bot_override
    a.dependency_overrides[_get_wizard_service] = _wizard_override
    yield a
    a.dependency_overrides.clear()


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


async def _create_user(client: AsyncClient, owner_token: str, username: str, role: str) -> str:
    r = await client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": f"{username}pw9", "role": role},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 201, r.text
    lr = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": f"{username}pw9"},
    )
    return str(lr.json()["tokens"]["access_token"])


# ---------------------------------------------------------------------------
# GET /bots/check-app-id (FEISHU-05)
# ---------------------------------------------------------------------------


async def test_check_app_id_returns_available_when_no_conflict(
    client: AsyncClient,
) -> None:
    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/check-app-id?app_id=cli_brand_new",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["conflict_bot"] is None


async def test_check_app_id_returns_conflict_bot_name_when_app_id_in_use(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    session.add(Bot(name="alpha", feishu_app_id="cli_taken_id", tags=[]))
    await session.commit()
    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/check-app-id?app_id=cli_taken_id",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert body["conflict_bot"] == "alpha"


async def test_check_app_id_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/api/v1/bots/check-app-id?app_id=cli_x")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /bots/{name}/secret (FEISHU-04 + RBAC)
# ---------------------------------------------------------------------------


async def test_reset_secret_replaces_db_ciphertext_and_rewrites_env(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    """Initial secret + .env exist. After PATCH, both reflect new value."""
    initial_enc = encrypt_str("old-secret")
    bot = Bot(
        name="alpha",
        feishu_app_id="cli_xx",
        feishu_app_secret_enc=initial_enc,
        tags=[],
    )
    session.add(bot)
    await session.commit()
    fake_host.fs[HERMES_HOME / "profiles" / "alpha" / ".env"] = (
        "FEISHU_APP_ID=cli_xx\nFEISHU_APP_SECRET=old-secret\n"
    )

    token = await _bootstrap_owner(client)
    r = await client.patch(
        "/api/v1/bots/alpha/secret",
        json={"feishu_app_secret": "new-secret-9876"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["feishu_app_secret_last4"] == "9876"

    # .env rewritten on disk.
    env_body = fake_host.fs[HERMES_HOME / "profiles" / "alpha" / ".env"]
    assert "FEISHU_APP_SECRET=new-secret-9876" in env_body
    assert "old-secret" not in env_body


async def test_reset_secret_returns_404_when_bot_missing(client: AsyncClient) -> None:
    token = await _bootstrap_owner(client)
    r = await client.patch(
        "/api/v1/bots/ghost/secret",
        json={"feishu_app_secret": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


async def test_reset_secret_requires_editor_role(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add(Bot(name="alpha", feishu_app_secret_enc=encrypt_str("old"), tags=[]))
    await session.commit()
    owner_token = await _bootstrap_owner(client)
    viewer_token = await _create_user(client, owner_token, "viewer1", "Viewer")
    r = await client.patch(
        "/api/v1/bots/alpha/secret",
        json={"feishu_app_secret": "new-secret"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /bots/{name}/wizard/run (FEISHU-02 + secret-from-DB)
# ---------------------------------------------------------------------------


async def test_run_wizard_returns_404_when_bot_not_found(client: AsyncClient) -> None:
    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/ghost/wizard/run?feishu_app_id=cli_xxxxlonger",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


async def test_run_wizard_returns_422_when_secret_not_set(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Bot exists but feishu_app_secret_enc is null — must reject."""
    session.add(Bot(name="alpha", feishu_app_secret_enc=None, tags=[]))
    await session.commit()
    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/alpha/wizard/run?feishu_app_id=cli_xxxxlonger",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


async def test_run_wizard_streams_sse_events_with_correct_media_type(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    from app.adapters import CliResult, ProcessInfo

    enc = encrypt_str("secret-value-12")
    session.add(
        Bot(
            name="alpha",
            feishu_app_id="cli_xxxxlonger",
            feishu_app_secret_enc=enc,
            tags=[],
        )
    )
    await session.commit()

    # Pretend everything is already configured — pure idempotent retry path.
    fake_host.fs[HERMES_HOME / "profiles" / "alpha" / "config.yaml"] = "version: 1\n"
    fake_host.fs[HERMES_HOME / "profiles" / "alpha" / ".env"] = "FEISHU_APP_ID=cli_xxxxlonger\n"
    fake_host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(0, "Version: 1.5.5\n", ""),
    )
    fake_host.fs[HERMES_HOME / "profiles" / "alpha" / "gateway.yaml"] = "x: 1\n"
    fake_host.fs[HERMES_HOME / "profiles" / "alpha" / "gateway.pid"] = "42\n"
    fake_host.process_table[42] = ProcessInfo(pid=42, cmdline=["hermes"], environ={}, is_alive=True)
    fake_host.queue_response(["-p", "alpha", "doctor"], CliResult(0, "ok", ""))

    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/alpha/wizard/run?feishu_app_id=cli_xxxxlonger",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    # Each SSE frame is a 'data: ...' line; should have at least 7 step-success
    # frames + 1 'done' frame.
    assert body.count('"status": "success"') >= 7
    assert '"status": "done"' in body


async def test_run_wizard_falls_back_to_db_app_id_when_query_is_empty(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    from app.adapters import CliResult, ProcessInfo

    enc = encrypt_str("secret-value-12")
    session.add(
        Bot(
            name="alpha",
            feishu_app_id="cli_from_db_long",
            feishu_app_secret_enc=enc,
            tags=[],
        )
    )
    await session.commit()

    fake_host.fs[HERMES_HOME / "profiles" / "alpha" / "config.yaml"] = "version: 1\n"
    fake_host.fs[HERMES_HOME / "profiles" / "alpha" / ".env"] = "FEISHU_APP_ID=cli_from_db_long\n"
    fake_host.queue_command_response(
        ["python3", "-m", "pip", "show", "lark-oapi"],
        CliResult(0, "Version: 1.5.5\n", ""),
    )
    fake_host.fs[HERMES_HOME / "profiles" / "alpha" / "gateway.yaml"] = "x: 1\n"
    fake_host.fs[HERMES_HOME / "profiles" / "alpha" / "gateway.pid"] = "42\n"
    fake_host.process_table[42] = ProcessInfo(pid=42, cmdline=["hermes"], environ={}, is_alive=True)
    fake_host.queue_response(["-p", "alpha", "doctor"], CliResult(0, "ok", ""))

    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/alpha/wizard/run?feishu_app_id=",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert '"status": "error"' not in r.text
    assert '"status": "done"' in r.text


async def test_run_wizard_does_not_accept_secret_in_query_params(
    client: AsyncClient, session: AsyncSession
) -> None:
    """SECURITY: secret must NOT be a query param (would land in access logs)."""
    session.add(Bot(name="alpha", feishu_app_secret_enc=None, tags=[]))
    await session.commit()
    token = await _bootstrap_owner(client)
    # Even if the caller tries to pass feishu_app_secret, the server should
    # ignore it (FastAPI rejects unknown queries silently). The 422 stems
    # from bot.feishu_app_secret_enc being NULL.
    r = await client.get(
        "/api/v1/bots/alpha/wizard/run"
        "?feishu_app_id=cli_xxxxlonger&feishu_app_secret=should_be_ignored",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    # Plaintext must not appear in error response either.
    assert "should_be_ignored" not in r.text


# ---------------------------------------------------------------------------
# PATCH /bots/{name}/feishu-credentials
# ---------------------------------------------------------------------------


async def test_update_feishu_credentials_persists_secret_and_rewrites_env(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    session.add(Bot(name="alpha", tags=[]))
    await session.commit()
    token = await _bootstrap_owner(client)
    r = await client.patch(
        "/api/v1/bots/alpha/feishu-credentials",
        json={
            "feishu_app_id": "cli_brand_new",
            "feishu_app_secret": "supersecret-9876",
            "domain": "feishu",
            "connection_mode": "websocket",
            "group_strategy": "mention",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["feishu_app_id"] == "cli_brand_new"
    assert body["feishu_app_secret_last4"] == "9876"
    env = fake_host.fs[HERMES_HOME / "profiles" / "alpha" / ".env"]
    assert "FEISHU_APP_ID=cli_brand_new" in env
    assert "FEISHU_APP_SECRET=supersecret-9876" in env
    assert "FEISHU_CONNECTION_MODE=websocket" in env
    # Default domain (feishu) and default group_strategy (mention) → 不写入环境变量。
    assert "FEISHU_DOMAIN" not in env
    assert "FEISHU_GROUP_STRATEGY" not in env


async def test_update_feishu_credentials_writes_lark_and_group_when_non_default(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    session.add(Bot(name="alpha", tags=[]))
    await session.commit()
    token = await _bootstrap_owner(client)
    r = await client.patch(
        "/api/v1/bots/alpha/feishu-credentials",
        json={
            "feishu_app_id": "cli_x",
            "feishu_app_secret": "s",
            "domain": "lark",
            "connection_mode": "websocket",
            "group_strategy": "all",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    env = fake_host.fs[HERMES_HOME / "profiles" / "alpha" / ".env"]
    assert "FEISHU_DOMAIN=lark" in env
    assert "FEISHU_GROUP_STRATEGY=all" in env


async def test_update_feishu_credentials_409_when_app_id_belongs_to_other_bot(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    session.add(Bot(name="alpha", feishu_app_id="cli_taken", tags=[]))
    session.add(Bot(name="beta", tags=[]))
    await session.commit()
    token = await _bootstrap_owner(client)
    r = await client.patch(
        "/api/v1/bots/beta/feishu-credentials",
        json={"feishu_app_id": "cli_taken", "feishu_app_secret": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409
    assert "alpha" in r.json()["detail"]


async def test_update_feishu_credentials_allows_same_bot_to_overwrite(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    session.add(Bot(name="alpha", feishu_app_id="cli_same", tags=[]))
    await session.commit()
    token = await _bootstrap_owner(client)
    r = await client.patch(
        "/api/v1/bots/alpha/feishu-credentials",
        json={"feishu_app_id": "cli_same", "feishu_app_secret": "rotated-1234"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["feishu_app_secret_last4"] == "1234"


async def test_update_feishu_credentials_404_when_bot_missing(
    client: AsyncClient,
) -> None:
    token = await _bootstrap_owner(client)
    r = await client.patch(
        "/api/v1/bots/ghost/feishu-credentials",
        json={"feishu_app_id": "cli_x", "feishu_app_secret": "s"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


async def test_update_feishu_credentials_requires_editor_role(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add(Bot(name="alpha", tags=[]))
    await session.commit()
    owner_token = await _bootstrap_owner(client)
    viewer_token = await _create_user(client, owner_token, "viewer1", "Viewer")
    r = await client.patch(
        "/api/v1/bots/alpha/feishu-credentials",
        json={"feishu_app_id": "cli_x", "feishu_app_secret": "s"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /bots/{name}/lark-app/init — SSE
# ---------------------------------------------------------------------------


async def test_lark_app_init_streams_lines_and_extracts_url(
    client: AsyncClient, app: FastAPI, session: AsyncSession
) -> None:
    """Inject a fake stream and verify line, url, and done events."""
    from app.api.v1.wizard import _get_lark_init_stream

    session.add(Bot(name="alpha", tags=[]))
    await session.commit()

    async def fake_stream(**_: Any) -> AsyncIterator[str]:
        yield "欢迎使用 lark-cli\n"
        yield "█████ QR █████\n"
        yield "请在浏览器打开: https://open.feishu.cn/page/cli?token=xyz123\n"

    app.dependency_overrides[_get_lark_init_stream] = lambda: fake_stream

    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/alpha/lark-app/init",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    assert '"type": "line"' in body
    assert "QR" in body
    assert '"type": "url"' in body
    assert "open.feishu.cn/page/cli?token=xyz123" in body
    assert '"type": "done"' in body


async def test_lark_app_init_emits_missing_when_binary_absent(
    client: AsyncClient, app: FastAPI, session: AsyncSession
) -> None:
    from app.api.v1.wizard import _get_lark_init_stream

    session.add(Bot(name="alpha", tags=[]))
    await session.commit()

    async def fake_stream(**_: Any) -> AsyncIterator[str]:
        yield "__lark_cli_missing__\n"

    app.dependency_overrides[_get_lark_init_stream] = lambda: fake_stream

    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/alpha/lark-app/init",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert '"type": "missing"' in r.text


async def test_lark_app_init_requires_editor_role(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add(Bot(name="alpha", tags=[]))
    await session.commit()
    owner_token = await _bootstrap_owner(client)
    viewer_token = await _create_user(client, owner_token, "viewer1", "Viewer")
    r = await client.get(
        "/api/v1/bots/alpha/lark-app/init",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403


async def test_lark_app_init_returns_404_when_bot_missing(client: AsyncClient) -> None:
    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/ghost/lark-app/init",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
