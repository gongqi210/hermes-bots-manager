"""Phase 5 management router integration tests."""

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


def _profile_config_path(name: str) -> Path:
    return HERMES_HOME / "profiles" / name / "config.yaml"


def _profile_env_path(name: str) -> Path:
    return HERMES_HOME / "profiles" / name / ".env"


def _profile_sessions_index_path(name: str) -> Path:
    return HERMES_HOME / "profiles" / name / "sessions" / "sessions.json"


# ---------------------------------------------------------------------------
# Model config
# ---------------------------------------------------------------------------


async def test_model_config_get_empty(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/foo/model-config", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "bot_name": "foo",
        "provider": None,
        "model": None,
        "base_url": None,
        "api_mode": None,
        "is_chatgpt_auth": False,
    }


async def test_model_config_put_chatgpt_auth_marks_flag(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    fake_host.fs[_profile_config_path("foo")] = "feishu:\n  domain: feishu\n"
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/model-config",
        json={
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "api_mode": "codex_responses",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_chatgpt_auth"] is True
    assert body["model"] == "gpt-5.5"
    written = yaml.safe_load(fake_host.fs[_profile_config_path("foo")])
    assert written["model"]["provider"] == "openai-codex"
    assert written["model"]["default"] == "gpt-5.5"
    assert written["feishu"]["domain"] == "feishu"  # preserved


async def test_model_config_put_preserves_unknown_keys(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    fake_host.fs[_profile_config_path("foo")] = (
        "model:\n  provider: openai\n  default: gpt-4o\nweird_future_key:\n  inner: value\n"
    )
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/model-config",
        json={
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "api_mode": "codex_responses",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    written = yaml.safe_load(fake_host.fs[_profile_config_path("foo")])
    assert written["weird_future_key"] == {"inner": "value"}


async def test_model_config_viewer_cannot_put(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    owner_token = await _bootstrap_owner(client)
    viewer_token = await _make_user(client, owner_token, "viewer1", "Viewer")
    r = await client.put(
        "/api/v1/bots/foo/model-config",
        json={
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "api_mode": "codex_responses",
        },
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


async def test_workspace_get_unset(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_bot(session)
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/bots/foo/workspace", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unset"
    assert body["cwd"] is None


async def test_workspace_put_rejects_relative_path(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/workspace",
        json={"cwd": "relative/path"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


async def test_workspace_put_real_dir_marks_ok(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession, tmp_path: Path
) -> None:
    await _seed_bot(session)
    fake_host.fs[_profile_env_path("foo")] = "FEISHU_APP_ID=cli_x\n"
    fake_host.fs[_profile_sessions_index_path("foo")] = '{"old":"session"}\n'
    target = tmp_path / "ws"
    target.mkdir()
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/workspace",
        json={"cwd": str(target)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cwd"] == str(target)
    assert body["status"] == "ok"
    written = yaml.safe_load(fake_host.fs[_profile_config_path("foo")])
    assert written["terminal"]["cwd"] == str(target)
    env_text = fake_host.fs[_profile_env_path("foo")]
    assert "FEISHU_APP_ID=cli_x" in env_text
    assert f"TERMINAL_CWD={target}" in env_text
    assert fake_host.fs[_profile_sessions_index_path("foo")] == "{}\n"


async def test_workspace_put_missing_dir_marks_error(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession, tmp_path: Path
) -> None:
    await _seed_bot(session)
    missing = tmp_path / "does-not-exist"
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/workspace",
        json={"cwd": str(missing)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["exists"] is False


async def test_workspace_put_clear_with_null(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession, tmp_path: Path
) -> None:
    await _seed_bot(session)
    fake_host.fs[_profile_config_path("foo")] = "terminal:\n  cwd: /some/path\n"
    fake_host.fs[_profile_env_path("foo")] = "FEISHU_APP_ID=cli_x\nTERMINAL_CWD=/some/path\n"
    fake_host.fs[_profile_sessions_index_path("foo")] = '{"old":"session"}\n'
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/workspace",
        json={"cwd": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    written = yaml.safe_load(fake_host.fs[_profile_config_path("foo")])
    assert "terminal" not in written
    env_text = fake_host.fs[_profile_env_path("foo")]
    assert "FEISHU_APP_ID=cli_x" in env_text
    assert "TERMINAL_CWD=" not in env_text
    assert fake_host.fs[_profile_sessions_index_path("foo")] == "{}\n"


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


def _seed_skill(
    fake_host: InMemoryHostOps,
    profile: str,
    skill_dir: str,
    *,
    name: str | None = None,
    description: str = "",
    category: str | None = None,
) -> None:
    front = f"---\nname: {name or Path(skill_dir).name}\n"
    if description:
        front += f"description: {description}\n"
    if category:
        front += f"category: {category}\n"
    front += "---\nbody\n"
    fake_host.fs[HERMES_HOME / "profiles" / profile / "skills" / skill_dir / "SKILL.md"] = front


async def test_skills_get_lists_profile_skills(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    _seed_skill(fake_host, "foo", "weather", description="weather skill")
    _seed_skill(
        fake_host,
        "foo",
        "shellrunner",
        description="execute shell commands",
        category="shell",
    )
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/bots/foo/skills", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    names = [s["name"] for s in body["skills"]]
    assert names == ["shellrunner", "weather"]
    by_name = {s["name"]: s for s in body["skills"]}
    assert by_name["shellrunner"]["dangerous"] is True
    assert by_name["weather"]["dangerous"] is False
    assert all(s["enabled"] for s in body["skills"])


async def test_skills_get_supports_category_nested_skill_dirs(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    _seed_skill(fake_host, "foo", "software-development/plan", description="Plan work")
    token = await _bootstrap_owner(client)

    r = await client.get("/api/v1/bots/foo/skills", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["skills"]) == 1
    skill = body["skills"][0]
    assert skill["name"] == "plan"
    assert skill["category"] == "software-development"
    assert skill["description"] == "Plan work"
    assert skill["source"] == "profile"
    assert skill["enabled"] is True
    assert skill["dangerous"] is False


async def test_skills_put_persists_disabled_list(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    _seed_skill(fake_host, "foo", "weather", description="weather skill")
    _seed_skill(fake_host, "foo", "calc", description="basic calculator")
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/skills",
        json={"disabled": ["weather"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {s["name"]: s for s in body["skills"]}
    assert by_name["weather"]["enabled"] is False
    assert by_name["calc"]["enabled"] is True
    written = yaml.safe_load(fake_host.fs[_profile_config_path("foo")])
    assert written["skills"]["disabled"] == ["weather"]


async def test_skills_put_dangerous_enable_requires_confirm(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    _seed_skill(fake_host, "foo", "shellrunner", description="execute shell commands")
    fake_host.fs[_profile_config_path("foo")] = "skills:\n  disabled:\n    - shellrunner\n"
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/skills",
        json={"disabled": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


async def test_skills_put_dangerous_enable_with_confirm_succeeds(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    _seed_skill(fake_host, "foo", "shellrunner", description="execute shell commands")
    fake_host.fs[_profile_config_path("foo")] = "skills:\n  disabled:\n    - shellrunner\n"
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/bots/foo/skills",
        json={"disabled": [], "confirm_name": "foo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {s["name"]: s for s in body["skills"]}
    assert by_name["shellrunner"]["enabled"] is True


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def test_health_combines_signals(
    client: AsyncClient, fake_host: InMemoryHostOps, session: AsyncSession
) -> None:
    await _seed_bot(session)
    fake_host.fs[HERMES_HOME / "profiles" / "foo" / ".env"] = (
        "FEISHU_APP_ID=cli_x\nFEISHU_APP_SECRET=secret\n"
    )
    fake_host.fs[_profile_config_path("foo")] = (
        "model:\n  provider: openai-codex\n  default: gpt-5.5\n"
        "  base_url: https://chatgpt.com/backend-api/codex\n"
        "  api_mode: codex_responses\n"
    )
    _seed_skill(fake_host, "foo", "weather", description="weather skill")
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/bots/foo/health", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bot_name"] == "foo"
    assert body["model_configured"] is True
    assert body["skills_total"] == 1
    assert body["skills_enabled"] == 1
    # Gateway not started in test → state should be stopped/unconfigured.
    assert body["gateway_state"] in {"stopped", "unconfigured"}
    assert body["overall"] in {"warning", "error"}


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def test_audit_list_returns_recent_rows_and_filters(
    client: AsyncClient, session: AsyncSession
) -> None:
    token = await _bootstrap_owner(client)
    # Generate two audit rows: one success, one failure (404).
    await client.put(
        "/api/v1/bots/foo/workspace",
        json={"cwd": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.put(
        "/api/v1/bots/ghost/workspace",
        json={"cwd": None},
        headers={"Authorization": f"Bearer {token}"},
    )

    r = await client.get(
        "/api/v1/audit?limit=10",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 2
    assert all(row["method"] in {"PUT", "POST", "PATCH", "DELETE"} for row in rows)

    r2 = await client.get(
        "/api/v1/audit?limit=10&result=failure",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    rows2 = r2.json()
    assert all(row["result"] == "failure" for row in rows2)


async def test_unknown_bot_returns_404(client: AsyncClient) -> None:
    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/bots/ghost/model-config",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
