"""Skills upload API integration tests (SKILLS-07)."""

from __future__ import annotations

import io
import zipfile
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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def hermes_home(tmp_path: Path) -> Path:
    """A real writable hermes home directory for upload tests."""
    h = tmp_path / "hermes"
    h.mkdir(parents=True, exist_ok=True)
    return h


@pytest_asyncio.fixture
async def fake_host(hermes_home: Path) -> InMemoryHostOps:
    """InMemoryHostOps but with the real hermes_home path so profile lookups work."""
    host = InMemoryHostOps()
    # Seed the profiles dir so list_profiles returns something
    bot_profile_config = hermes_home / "profiles" / "foo" / "config.yaml"
    bot_profile_config.parent.mkdir(parents=True, exist_ok=True)
    bot_profile_config.write_text("")
    host.fs[bot_profile_config] = ""
    return host


@pytest_asyncio.fixture
async def app(engine: Any, fake_host: InMemoryHostOps, hermes_home: Path) -> AsyncIterator[FastAPI]:
    from app.main import create_app

    a = create_app()
    a.state.cli = HermesCliAdapter(fake_host)
    a.state.host = fake_host
    a.state.fs = ProfileFsAdapter(fake_host, hermes_home=hermes_home)
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


def _make_zip(files: dict[str, bytes]) -> bytes:
    """Create a zip in memory from a dict of {filename: content}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_upload_valid_skill_zip(client: AsyncClient, session: AsyncSession) -> None:
    """POST /bots/{name}/skills/upload with valid zip returns 200 with skill metadata."""
    await _seed_bot(session)
    token = await _bootstrap_owner(client)

    zip_data = _make_zip({
        "skill.yaml": b"name: my-skill\ndescription: test skill\n",
        "main.py": b"print('hello')\n",
    })

    r = await client.post(
        "/api/v1/bots/foo/skills/upload",
        files={"file": ("my-skill.zip", zip_data, "application/zip")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "my-skill"
    assert data["enabled"] is True


async def test_upload_exceeds_size_limit(client: AsyncClient, session: AsyncSession) -> None:
    """Upload with size > 10 MB returns 422."""
    await _seed_bot(session)
    token = await _bootstrap_owner(client)

    # Create a zip that exceeds 10 MB (store raw bytes, not compressed)
    big_content = b"x" * (11 * 1024 * 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("skill.yaml", b"name: big\n")
        zf.writestr("big.bin", big_content)
    zip_data = buf.getvalue()

    r = await client.post(
        "/api/v1/bots/foo/skills/upload",
        files={"file": ("big.zip", zip_data, "application/zip")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text
    assert "10 MB" in r.json()["detail"]


async def test_upload_zip_slip_rejected(client: AsyncClient, session: AsyncSession) -> None:
    """Upload with path-traversal entry returns 422."""
    await _seed_bot(session)
    token = await _bootstrap_owner(client)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skill.yaml", b"name: evil\n")
        zf.writestr("../../etc/passwd", b"root:x:0:0\n")
    zip_data = buf.getvalue()

    r = await client.post(
        "/api/v1/bots/foo/skills/upload",
        files={"file": ("evil.zip", zip_data, "application/zip")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text
    assert "路径不安全" in r.json()["detail"]


async def test_upload_zip_slip_sibling_prefix_rejected(
    client: AsyncClient,
    session: AsyncSession,
    hermes_home: Path,
) -> None:
    """Sibling paths with the same string prefix must not pass zip-slip validation."""
    await _seed_bot(session)
    token = await _bootstrap_owner(client)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skill.yaml", b"name: evil\n")
        zf.writestr("../evil_sibling/SKILL.md", b"name: evil_sibling\n")
    zip_data = buf.getvalue()

    r = await client.post(
        "/api/v1/bots/foo/skills/upload",
        files={"file": ("evil.zip", zip_data, "application/zip")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text
    assert not (hermes_home / "profiles" / "foo" / "skills" / "evil_sibling").exists()


async def test_upload_missing_skill_yaml_rejected(client: AsyncClient, session: AsyncSession) -> None:
    """Upload without skill.yaml returns 422."""
    await _seed_bot(session)
    token = await _bootstrap_owner(client)

    zip_data = _make_zip({"main.py": b"print('hello')\n"})

    r = await client.post(
        "/api/v1/bots/foo/skills/upload",
        files={"file": ("noskill.zip", zip_data, "application/zip")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text
    assert "skill.yaml" in r.json()["detail"]


async def test_upload_requires_owner_role(client: AsyncClient, session: AsyncSession) -> None:
    """Upload with Editor role returns 403."""
    await _seed_bot(session)
    owner_token = await _bootstrap_owner(client)
    editor_token = await _make_user(client, owner_token, "editor1", "Editor")

    zip_data = _make_zip({"skill.yaml": b"name: test\n"})

    r = await client.post(
        "/api/v1/bots/foo/skills/upload",
        files={"file": ("skill.zip", zip_data, "application/zip")},
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert r.status_code == 403, r.text
