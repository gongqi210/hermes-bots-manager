from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def app(engine: Any) -> FastAPI:
    from app.main import create_app

    return create_app()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_bootstrap_then_conflict(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "hello12345"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["role"] == "Owner"
    assert body["tokens"]["access_expires_in"] == 7200
    assert body["tokens"]["refresh_expires_in"] == 604800

    r2 = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "x", "password": "hello12345"},
    )
    assert r2.status_code == 409


async def test_login_wrong_password(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "hello12345"},
    )
    r = await client.post("/api/v1/auth/login", json={"username": "owner", "password": "WRONG123"})
    assert r.status_code == 401


async def test_login_unknown_user(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "hello12345"},
    )
    r = await client.post(
        "/api/v1/auth/login", json={"username": "ghost", "password": "hello12345"}
    )
    assert r.status_code == 401


async def test_me_requires_bearer(client: AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_full_flow_me_logout(client: AsyncClient) -> None:
    br = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "hello12345"},
    )
    token = br.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["username"] == "owner"

    lo = await client.post("/api/v1/auth/logout", headers=headers)
    assert lo.status_code == 200

    me2 = await client.get("/api/v1/auth/me", headers=headers)
    assert me2.status_code == 401


async def test_refresh(client: AsyncClient) -> None:
    br = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "hello12345"},
    )
    refresh_token = br.json()["tokens"]["refresh_token"]

    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert r.status_code == 200
    assert "access_token" in r.json()

    # Using an access token as refresh → 401.
    access_token = br.json()["tokens"]["access_token"]
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
    assert r2.status_code == 401


async def test_viewer_cannot_create_user(client: AsyncClient) -> None:
    br = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "hello12345"},
    )
    owner_token = br.json()["tokens"]["access_token"]

    # Owner creates a Viewer.
    r = await client.post(
        "/api/v1/auth/users",
        json={"username": "viewer", "password": "viewpass9", "role": "Viewer"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 201, r.text

    # Viewer logs in, tries to create another user → 403.
    vl = await client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "viewpass9"}
    )
    viewer_token = vl.json()["tokens"]["access_token"]
    r2 = await client.post(
        "/api/v1/auth/users",
        json={"username": "x", "password": "xyz98765", "role": "Viewer"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r2.status_code == 403


async def test_admin_cannot_create_user(client: AsyncClient) -> None:
    br = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "hello12345"},
    )
    owner_token = br.json()["tokens"]["access_token"]
    await client.post(
        "/api/v1/auth/users",
        json={"username": "admin1", "password": "adminpw9", "role": "Admin"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    al = await client.post(
        "/api/v1/auth/login", json={"username": "admin1", "password": "adminpw9"}
    )
    admin_token = al.json()["tokens"]["access_token"]
    r = await client.post(
        "/api/v1/auth/users",
        json={"username": "x", "password": "xyz98765", "role": "Viewer"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # PRD §5.9.1: only Owner can manage users.
    assert r.status_code == 403
