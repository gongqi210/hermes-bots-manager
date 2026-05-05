"""POST /api/v1/ws-token integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import ProfileFsAdapter
from app.auth.jwt_utils import decode_ws_token
from app.models.bot import Bot
from tests.adapters.fakes import InMemoryHostOps

HERMES_HOME = Path("/h")


@pytest_asyncio.fixture
async def fake_host() -> InMemoryHostOps:
    return InMemoryHostOps()


@pytest_asyncio.fixture
async def app(engine: Any, fake_host: InMemoryHostOps) -> FastAPI:
    from app.main import create_app

    a = create_app()
    a.state.fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
    return a


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


async def test_w1_post_ws_token_returns_60s_token(
    client: AsyncClient, session: AsyncSession
) -> None:
    session.add(Bot(name="foo", tags=[]))
    await session.commit()
    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/ws-token",
        json={"bot_name": "foo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expires_in"] == 60
    payload = decode_ws_token(body["token"], bot_name="foo")
    assert payload["aud"] == "ws-gateway-logs:foo"
    assert payload["type"] == "ws"


async def test_w2_unauthenticated_returns_401(client: AsyncClient) -> None:
    r = await client.post("/api/v1/ws-token", json={"bot_name": "foo"})
    assert r.status_code == 401


async def test_w3_unknown_bot_returns_404(client: AsyncClient) -> None:
    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/ws-token",
        json={"bot_name": "nope"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404, r.text


async def test_w4_post_ws_token_allows_filesystem_profile_without_db_row(
    client: AsyncClient, fake_host: InMemoryHostOps
) -> None:
    fake_host.fs[HERMES_HOME / "config.yaml"] = "model: test\n"

    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/ws-token",
        json={"bot_name": "default"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    payload = decode_ws_token(r.json()["token"], bot_name="default")
    assert payload["aud"] == "ws-gateway-logs:default"
