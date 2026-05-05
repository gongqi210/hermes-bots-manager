"""Allowlist REST router integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import HermesCliAdapter, ProfileFsAdapter
from app.models.bot import Bot
from tests.adapters.fakes import InMemoryHostOps

HERMES_HOME = Path("/h")


@pytest_asyncio.fixture
async def fake_host() -> InMemoryHostOps:
    return InMemoryHostOps()


@pytest_asyncio.fixture
async def app(engine: Any, fake_host: InMemoryHostOps) -> AsyncIterator[FastAPI]:
    from app.main import create_app

    a = create_app()
    a.state.cli = HermesCliAdapter(fake_host)
    a.state.host = fake_host
    a.state.fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
    yield a


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _bootstrap_owner(client: AsyncClient) -> str:
    r = await client.post(
        "/api/v1/auth/bootstrap", json={"username": "owner", "password": "ownerpw9"}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["tokens"]["access_token"])


async def _make_user(client: AsyncClient, owner_token: str, username: str, role: str) -> str:
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


async def test_a1_get_allowlist_returns_users_from_env(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    fake_host.fs[HERMES_HOME / "profiles" / "foo" / ".env"] = (
        "FEISHU_APP_ID=cli_x\nFEISHU_APP_SECRET=secret\nFEISHU_ALLOWED_USERS=ou_a,ou_b,ou_c\n"
    )
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/bots/foo/allowlist", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bot_name"] == "foo"
    assert body["users"] == ["ou_a", "ou_b", "ou_c"]


async def test_a2_put_allowlist_writes_env_and_returns_users(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    fake_host.fs[HERMES_HOME / "profiles" / "foo" / ".env"] = (
        "FEISHU_APP_ID=cli_x\nFEISHU_APP_SECRET=secret\n"
    )
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/allowlist",
        json={"users": ["ou_x", "ou_y"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["users"] == ["ou_x", "ou_y"]


async def test_a3_put_preserves_other_env_keys(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    fake_host.fs[HERMES_HOME / "profiles" / "foo" / ".env"] = (
        "FEISHU_APP_ID=cli_x\nFEISHU_APP_SECRET=secret123\n"
    )
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/allowlist",
        json={"users": ["ou_z"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    written = fake_host.fs[HERMES_HOME / "profiles" / "foo" / ".env"]
    assert "FEISHU_APP_ID=cli_x" in written
    assert "FEISHU_APP_SECRET=secret123" in written
    assert "FEISHU_ALLOWED_USERS=ou_z" in written


async def test_a4_put_with_comma_in_entry_returns_422(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    fake_host.fs[HERMES_HOME / "profiles" / "foo" / ".env"] = (
        "FEISHU_APP_ID=cli_x\nFEISHU_APP_SECRET=secret\n"
    )
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/allowlist",
        json={"users": ["ou_a,ou_smuggled"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text


async def test_a5_get_allowlist_returns_empty_when_env_missing(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/bots/foo/allowlist", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["users"] == []


async def test_a6_get_allows_filesystem_profile_without_db_row(
    client: AsyncClient, fake_host: InMemoryHostOps
) -> None:
    fake_host.fs[HERMES_HOME / "config.yaml"] = "model: test\n"
    fake_host.fs[HERMES_HOME / ".env"] = "FEISHU_ALLOWED_USERS=ou_default\n"

    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/default/allowlist",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["users"] == ["ou_default"]


async def test_a7_put_allows_filesystem_profile_without_db_row(
    client: AsyncClient, fake_host: InMemoryHostOps
) -> None:
    fake_host.fs[HERMES_HOME / "config.yaml"] = "model: test\n"

    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/default/allowlist",
        json={"users": ["ou_new"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["users"] == ["ou_new"]
    assert "FEISHU_ALLOWED_USERS=ou_new" in fake_host.fs[HERMES_HOME / ".env"]


async def test_a_rbac_viewer_can_read_but_not_write(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    fake_host.fs[HERMES_HOME / "profiles" / "foo" / ".env"] = (
        "FEISHU_APP_ID=cli_x\nFEISHU_ALLOWED_USERS=ou_a\n"
    )
    session.add(Bot(name="foo", tags=[]))
    await session.commit()

    owner_token = await _bootstrap_owner(client)
    viewer_token = await _make_user(client, owner_token, "viewer1", "Viewer")

    r = await client.get(
        "/api/v1/bots/foo/allowlist",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 200

    r2 = await client.put(
        "/api/v1/bots/foo/allowlist",
        json={"users": ["ou_b"]},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r2.status_code == 403, r2.text


async def test_a_unknown_bot_returns_404(client: AsyncClient) -> None:
    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/ghost/allowlist", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 404
