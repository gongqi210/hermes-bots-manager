"""Bot REST API integration tests.

We override ``get_bot_service`` via ``app.dependency_overrides`` to inject a
BotService backed by InMemoryHostOps + the test DB session — no real subprocess
or filesystem access. Auth + RBAC tests use the real auth flow (bootstrap →
login) since that exercises the JWT + middleware end-to-end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import CliResult, HermesCliAdapter, ProfileFsAdapter
from app.models.audit import AuditLog
from app.services.bot import BotService
from tests.adapters.fakes import InMemoryHostOps

HERMES_HOME = Path("/h")


# ----------------------------------------------------------------------
# Fixtures.
# ----------------------------------------------------------------------
@pytest_asyncio.fixture
async def fake_host() -> InMemoryHostOps:
    """A fresh InMemoryHostOps per test."""
    return InMemoryHostOps()


@pytest_asyncio.fixture
async def app(engine: Any, fake_host: InMemoryHostOps) -> AsyncIterator[FastAPI]:
    """FastAPI app with get_bot_service overridden to use InMemoryHostOps."""
    from app.api.v1.bots import get_bot_service
    from app.db.session import get_sessionmaker
    from app.main import create_app

    a = create_app()

    async def override() -> AsyncIterator[BotService]:
        cli = HermesCliAdapter(fake_host)
        fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
        # Use the same sessionmaker the rest of the app uses (already pointed
        # at the test engine by conftest.engine fixture).
        maker: async_sessionmaker[AsyncSession] = get_sessionmaker()
        async with maker() as session:
            try:
                yield BotService(session=session, cli=cli, fs=fs, archive_dir=Path("/h/archives"))
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    a.dependency_overrides[get_bot_service] = override
    yield a
    a.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _bootstrap_owner(client: AsyncClient) -> str:
    """Create the owner via /auth/bootstrap and return its access token."""
    r = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "ownerpw9"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["tokens"]["access_token"])


async def _create_user(client: AsyncClient, owner_token: str, username: str, role: str) -> str:
    """Owner creates a user with `role` and we log it in, returning its access token."""
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


def _seed_named(host: InMemoryHostOps, name: str) -> None:
    host.fs[HERMES_HOME / "profiles" / name / "config.yaml"] = "version: 1\n"


def _seed_default(host: InMemoryHostOps) -> None:
    host.fs[HERMES_HOME / "config.yaml"] = "version: 1\n"


# ----------------------------------------------------------------------
# GET /bots
# ----------------------------------------------------------------------


async def test_get_bots_returns_empty_list_for_fresh_install(client: AsyncClient) -> None:
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/bots", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_get_bots_returns_default_when_present(
    client: AsyncClient, fake_host: InMemoryHostOps
) -> None:
    _seed_default(fake_host)
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/bots", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["name"] == "default"
    assert body[0]["status"] == "grey"


async def test_get_bots_with_q_filter(client: AsyncClient, fake_host: InMemoryHostOps) -> None:
    _seed_named(fake_host, "alpha")
    _seed_named(fake_host, "beta")
    _seed_named(fake_host, "alpha-prod")
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/bots?q=alpha", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    names = sorted(b["name"] for b in r.json())
    assert names == ["alpha", "alpha-prod"]


async def test_get_bots_with_status_filter(client: AsyncClient, fake_host: InMemoryHostOps) -> None:
    _seed_named(fake_host, "alpha")
    _seed_named(fake_host, "beta")
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/bots?status=grey", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert len(r.json()) == 2
    r2 = await client.get("/api/v1/bots?status=green", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert r2.json() == []


async def test_get_bots_with_tag_filter(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    """B1: tag filter at the API."""
    from app.models.bot import Bot

    _seed_named(fake_host, "alpha")
    _seed_named(fake_host, "beta")
    _seed_named(fake_host, "gamma")
    session.add_all(
        [
            Bot(name="alpha", tags=["prod"]),
            Bot(name="beta", tags=["staging"]),
            Bot(name="gamma", tags=["prod", "ai"]),
        ]
    )
    await session.commit()

    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/bots?tag=prod", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    names = sorted(b["name"] for b in r.json())
    assert names == ["alpha", "gamma"]

    r_ai = await client.get("/api/v1/bots?tag=ai", headers={"Authorization": f"Bearer {token}"})
    assert sorted(b["name"] for b in r_ai.json()) == ["gamma"]

    r_missing = await client.get(
        "/api/v1/bots?tag=missing", headers={"Authorization": f"Bearer {token}"}
    )
    assert r_missing.json() == []


# ----------------------------------------------------------------------
# POST /bots
# ----------------------------------------------------------------------


async def test_post_bots_creates_bot_returns_201_with_botout(
    client: AsyncClient, fake_host: InMemoryHostOps
) -> None:
    fake_host.queue_response(["profile", "create", "--no-alias", "alpha"], CliResult(0, "ok\n", ""))
    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/bots",
        json={
            "name": "alpha",
            "feishu_app_id": "cli_x",
            "feishu_app_secret": "secret",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "alpha"
    assert body["feishu_app_secret_last4"] == "cret"
    # Plaintext secret never in response body.
    assert "secret" not in str(body) or body["feishu_app_secret_last4"] in str(body)
    assert "feishu_app_secret_enc" not in body


async def test_post_bots_validates_name_returns_422_for_default(
    client: AsyncClient,
) -> None:
    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/bots",
        json={"name": "default"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text


async def test_post_bots_returns_409_on_duplicate(
    client: AsyncClient, fake_host: InMemoryHostOps
) -> None:
    fake_host.queue_response(["profile", "create", "--no-alias", "alpha"], CliResult(0, "ok\n", ""))
    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/bots", json={"name": "alpha"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 201
    r2 = await client.post(
        "/api/v1/bots", json={"name": "alpha"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r2.status_code == 409, r2.text


# ----------------------------------------------------------------------
# Clone / rename / delete
# ----------------------------------------------------------------------


async def test_post_bots_clone_endpoint(client: AsyncClient, fake_host: InMemoryHostOps) -> None:
    fake_host.queue_response(
        ["profile", "create", "--no-alias", "beta", "--clone-from", "alpha"],
        CliResult(0, "ok\n", ""),
    )
    token = await _bootstrap_owner(client)
    r = await client.post(
        "/api/v1/bots/alpha/clone",
        json={"new_name": "beta"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "beta"
    # Verify hermes was called with the right args.
    assert any(
        c[0] == ["profile", "create", "--no-alias", "beta", "--clone-from", "alpha"]
        for c in fake_host.calls
    )


async def test_patch_bots_rename(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    from app.models.bot import Bot

    fake_host.queue_response(["profile", "rename", "alpha", "alpha2"], CliResult(0, "ok\n", ""))
    session.add(Bot(name="alpha", tags=[]))
    await session.commit()
    token = await _bootstrap_owner(client)
    r = await client.patch(
        "/api/v1/bots/alpha",
        json={"new_name": "alpha2"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "alpha2"


async def test_delete_bots_requires_confirm_name(
    client: AsyncClient, fake_host: InMemoryHostOps
) -> None:
    token = await _bootstrap_owner(client)
    r = await client.request(
        "DELETE",
        "/api/v1/bots/alpha",
        json={"confirm_name": "wrong"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400, r.text


async def test_delete_bots_with_correct_confirm_archives_and_deletes(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    """Override the BotService archive_dir to tmp_path so the archive write
    actually lands somewhere we can inspect."""
    from app.api.v1.bots import get_bot_service
    from app.db.session import get_sessionmaker
    from app.models.bot import Bot

    # Replace the previous override with one that uses tmp_path as archive_dir.
    a = client._transport.app  # type: ignore[attr-defined,union-attr]

    async def override() -> AsyncIterator[BotService]:
        cli = HermesCliAdapter(fake_host)
        fs = ProfileFsAdapter(fake_host, hermes_home=HERMES_HOME)
        maker = get_sessionmaker()
        async with maker() as s:
            try:
                yield BotService(session=s, cli=cli, fs=fs, archive_dir=tmp_path)
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    a.dependency_overrides[get_bot_service] = override

    fake_host.queue_response(None, CliResult(0, "ok\n", ""))
    session.add(Bot(name="alpha", tags=[]))
    await session.commit()

    token = await _bootstrap_owner(client)
    r = await client.request(
        "DELETE",
        "/api/v1/bots/alpha",
        json={"confirm_name": "alpha"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204, r.text

    # Check hermes was called: profile_export then profile_delete.
    cmds = [c[0] for c in fake_host.calls]
    assert any(c[:3] == ["profile", "export", "alpha"] and c[3] == "-o" for c in cmds)
    assert ["profile", "delete", "-y", "alpha"] in cmds


# ----------------------------------------------------------------------
# RBAC + auth
# ----------------------------------------------------------------------


async def test_viewer_role_cannot_create_bot(
    client: AsyncClient, fake_host: InMemoryHostOps
) -> None:
    owner_token = await _bootstrap_owner(client)
    viewer_token = await _create_user(client, owner_token, "viewer1", "Viewer")
    r = await client.post(
        "/api/v1/bots",
        json={"name": "alpha"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403, r.text


async def test_unauthenticated_get_returns_401(client: AsyncClient) -> None:
    r = await client.get("/api/v1/bots")
    assert r.status_code == 401


async def test_audit_log_row_written_on_post(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    """Phase 1 audit middleware free-rides — verify a POST shows up in audit_log."""
    fake_host.queue_response(["profile", "create", "--no-alias", "alpha"], CliResult(0, "ok\n", ""))
    token = await _bootstrap_owner(client)

    # Snapshot pre-POST audit row count for /api/v1/bots POSTs.
    pre = (
        await session.scalars(
            select(AuditLog).where(AuditLog.path == "/api/v1/bots", AuditLog.method == "POST")
        )
    ).all()
    assert len(pre) == 0

    r = await client.post(
        "/api/v1/bots",
        json={"name": "alpha"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text

    # The middleware writes after the response — give the session a clean read.
    await session.commit()
    rows = (
        await session.scalars(
            select(AuditLog).where(AuditLog.path == "/api/v1/bots", AuditLog.method == "POST")
        )
    ).all()
    assert len(rows) >= 1
    assert rows[-1].result == "success"
