"""Integration tests for workspace library CRUD endpoints and reuse endpoint (Task 3).

Tests cover:
1. GET /api/v1/workspace-library returns 200 with empty list for Viewer
2. POST /api/v1/workspace-library returns 201 for Admin; 403 for Editor
3. DELETE /api/v1/workspace-library/{id} returns 204 for Admin; 403 for Editor
4. GET /bots/{name}/workspace-options/reuse returns list of {bot_name, cwd} bots
5. Alembic 005 upgrades and downgrades cleanly (separate bash verification)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
import yaml
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


async def _seed_bot(session: AsyncSession, name: str = "foo") -> None:
    session.add(Bot(name=name, tags=[]))
    await session.commit()


def _write_profile_config(fake_host: InMemoryHostOps, name: str, cwd: str) -> None:
    """Write a config.yaml for a bot profile with terminal.cwd set."""
    config = {"terminal": {"cwd": cwd}}
    path = HERMES_HOME / "profiles" / name / "config.yaml"
    fake_host.fs[path] = yaml.safe_dump(config)


# ---------------------------------------------------------------------------
# Test 1: GET /workspace-library returns 200 for Viewer with empty list
# ---------------------------------------------------------------------------


async def test_workspace_library_list_empty_for_viewer(
    client: AsyncClient,
) -> None:
    owner_token = await _bootstrap_owner(client)
    viewer_token = await _make_user(client, owner_token, "viewer1", "Viewer")

    r = await client.get(
        "/api/v1/workspace-library",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# Test 2: POST /workspace-library returns 201 for Admin, 403 for Editor
# ---------------------------------------------------------------------------


async def test_workspace_library_post_admin_201(
    client: AsyncClient,
) -> None:
    owner_token = await _bootstrap_owner(client)
    admin_token = await _make_user(client, owner_token, "admin1", "Admin")

    r = await client.post(
        "/api/v1/workspace-library",
        json={"path": "/tmp/workspace-a", "label": "Test WS"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["path"] == "/tmp/workspace-a"
    assert data["label"] == "Test WS"
    assert "id" in data


async def test_workspace_library_post_editor_403(
    client: AsyncClient,
) -> None:
    owner_token = await _bootstrap_owner(client)
    editor_token = await _make_user(client, owner_token, "editor1", "Editor")

    r = await client.post(
        "/api/v1/workspace-library",
        json={"path": "/tmp/workspace-b", "label": None},
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Test 3: DELETE /workspace-library/{id} returns 204 for Admin, 403 for Editor
# ---------------------------------------------------------------------------


async def test_workspace_library_delete_admin_204(
    client: AsyncClient,
) -> None:
    owner_token = await _bootstrap_owner(client)
    admin_token = await _make_user(client, owner_token, "admin2", "Admin")

    create_r = await client.post(
        "/api/v1/workspace-library",
        json={"path": "/tmp/workspace-delete", "label": None},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_r.status_code == 201
    item_id = create_r.json()["id"]

    del_r = await client.delete(
        f"/api/v1/workspace-library/{item_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert del_r.status_code == 204

    # Verify gone
    list_r = await client.get(
        "/api/v1/workspace-library",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_r.json() == []


async def test_workspace_library_delete_editor_403(
    client: AsyncClient,
) -> None:
    owner_token = await _bootstrap_owner(client)
    admin_token = await _make_user(client, owner_token, "admin3", "Admin")
    editor_token = await _make_user(client, owner_token, "editor2", "Editor")

    create_r = await client.post(
        "/api/v1/workspace-library",
        json={"path": "/tmp/workspace-no-del", "label": None},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_r.status_code == 201
    item_id = create_r.json()["id"]

    del_r = await client.delete(
        f"/api/v1/workspace-library/{item_id}",
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert del_r.status_code == 403


# ---------------------------------------------------------------------------
# Test 4: GET /bots/{name}/workspace-options/reuse
# ---------------------------------------------------------------------------


async def test_workspace_reuse_returns_bots_with_cwd(
    client: AsyncClient,
    session: AsyncSession,
    fake_host: InMemoryHostOps,
) -> None:
    owner_token = await _bootstrap_owner(client)

    # Seed bots: foo has cwd, bar has cwd, baz has none
    await _seed_bot(session, "foo")
    await _seed_bot(session, "bar")
    await _seed_bot(session, "baz")

    _write_profile_config(fake_host, "foo", "/workspace/foo")
    _write_profile_config(fake_host, "bar", "/workspace/bar")
    # baz has no config

    # GET reuse from perspective of "baz"
    r = await client.get(
        "/api/v1/bots/baz/workspace-options/reuse",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 200
    options = r.json()
    bot_names = {o["bot_name"] for o in options}
    assert "foo" in bot_names
    assert "bar" in bot_names
    # baz itself excluded (it's the caller)
    assert "baz" not in bot_names
    # Verify cwd values
    foo_opt = next(o for o in options if o["bot_name"] == "foo")
    assert foo_opt["cwd"] == "/workspace/foo"
