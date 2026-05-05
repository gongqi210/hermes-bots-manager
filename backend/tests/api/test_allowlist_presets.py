"""Allowlist preset endpoints + extended health response (Phase 5 plan 05-05)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import HermesCliAdapter, ProfileFsAdapter
from app.models.bot import Bot
from app.models.user import User
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


async def _make_user(
    client: AsyncClient, owner_token: str, username: str, role: str
) -> str:
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


async def _bind_feishu_id(session: AsyncSession, username: str, feishu_id: str) -> None:
    await session.execute(
        update(User).where(User.username == username).values(feishu_user_id=feishu_id)
    )
    await session.commit()


async def _seed_profile(
    fake_host: InMemoryHostOps,
    session: AsyncSession,
    bot_name: str,
    *,
    env_text: str = "FEISHU_APP_ID=cli_x\nFEISHU_APP_SECRET=secret\n",
) -> None:
    fake_host.fs[HERMES_HOME / "profiles" / bot_name / ".env"] = env_text
    fake_host.fs[HERMES_HOME / "profiles" / bot_name / "config.yaml"] = (
        "model:\n  provider: openai\n  model: gpt-4\n"
    )
    session.add(Bot(name=bot_name, tags=[]))
    await session.commit()


# ──────────────────────────────────────────────────────────────────────────
# Test 1: GET /health response includes new fields
# ──────────────────────────────────────────────────────────────────────────


async def test_health_includes_phase5_overview_fields(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_profile(fake_host, session, "foo")
    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/foo/health", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "dangerous_skill_count" in body
    assert "shadowed_skill_count" in body
    assert "allowlist_preset" in body
    # Empty allowlist → preset="open"
    assert body["allowlist_preset"] == "open"


# ──────────────────────────────────────────────────────────────────────────
# Test 2: GET /allowlist/presets returns owner_admin populated
# ──────────────────────────────────────────────────────────────────────────


async def test_get_presets_returns_resolved_owner_admin(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_profile(
        fake_host,
        session,
        "foo",
        env_text="FEISHU_APP_ID=cli_x\nFEISHU_ALLOWED_USERS=ou_existing\n",
    )
    token = await _bootstrap_owner(client)
    # Bind owner's Feishu ID + add an Admin with another ID
    await _bind_feishu_id(session, "owner", "ou_owner")
    await _make_user(client, token, "admin1", "Admin")
    await _bind_feishu_id(session, "admin1", "ou_admin")

    r = await client.get(
        "/api/v1/bots/foo/allowlist/presets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["open"] == []
    assert sorted(body["owner_admin"]) == ["ou_admin", "ou_owner"]
    assert body["custom"] == ["ou_existing"]
    assert body["owner_admin_warning"] is None


# ──────────────────────────────────────────────────────────────────────────
# Test 3: GET /presets returns warning when no Owner/Admin has feishu_user_id
# ──────────────────────────────────────────────────────────────────────────


async def test_get_presets_warns_when_owner_admin_unresolved(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_profile(fake_host, session, "foo")
    token = await _bootstrap_owner(client)  # owner has no feishu_user_id

    r = await client.get(
        "/api/v1/bots/foo/allowlist/presets",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["owner_admin"] == []
    assert body["owner_admin_warning"]
    assert "OpenID" in body["owner_admin_warning"]


# ──────────────────────────────────────────────────────────────────────────
# Test 4: PUT preset=open clears allowlist (Admin role required)
# ──────────────────────────────────────────────────────────────────────────


async def test_put_preset_open_writes_empty_list_admin_only(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_profile(
        fake_host,
        session,
        "foo",
        env_text="FEISHU_APP_ID=cli_x\nFEISHU_ALLOWED_USERS=ou_a,ou_b\n",
    )
    token = await _bootstrap_owner(client)
    editor_token = await _make_user(client, token, "editor1", "Editor")

    # Editor blocked
    r_editor = await client.put(
        "/api/v1/bots/foo/allowlist/preset",
        json={"preset": "open"},
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert r_editor.status_code == 403, r_editor.text

    # Owner allowed
    r = await client.put(
        "/api/v1/bots/foo/allowlist/preset",
        json={"preset": "open"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["users"] == []
    written = fake_host.fs[HERMES_HOME / "profiles" / "foo" / ".env"]
    assert "FEISHU_ALLOWED_USERS=" in written
    # The line value must be empty after preset=open.
    line = next(
        ln
        for ln in written.splitlines()
        if ln.startswith("FEISHU_ALLOWED_USERS=")
    )
    assert line == "FEISHU_ALLOWED_USERS="


# ──────────────────────────────────────────────────────────────────────────
# Test 5: PUT preset=owner_admin writes resolved IDs
# ──────────────────────────────────────────────────────────────────────────


async def test_put_preset_owner_admin_writes_resolved_ids(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_profile(fake_host, session, "foo")
    token = await _bootstrap_owner(client)
    await _bind_feishu_id(session, "owner", "ou_owner")
    await _make_user(client, token, "admin1", "Admin")
    await _bind_feishu_id(session, "admin1", "ou_admin")

    r = await client.put(
        "/api/v1/bots/foo/allowlist/preset",
        json={"preset": "owner_admin"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["users"]) == ["ou_admin", "ou_owner"]

    # And health endpoint should now report preset="owner_admin"
    h = await client.get(
        "/api/v1/bots/foo/health", headers={"Authorization": f"Bearer {token}"}
    )
    assert h.status_code == 200
    assert h.json()["allowlist_preset"] == "owner_admin"


async def test_put_preset_owner_admin_422_when_unresolved(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_profile(fake_host, session, "foo")
    token = await _bootstrap_owner(client)  # no feishu_user_id bound
    r = await client.put(
        "/api/v1/bots/foo/allowlist/preset",
        json={"preset": "owner_admin"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text
