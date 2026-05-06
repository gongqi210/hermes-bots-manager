"""GET /api/v1/bots/{name}/logs/download integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters import HermesCliAdapter, ProfileFsAdapter
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


async def _bootstrap(client: AsyncClient) -> str:
    r = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "ownerpw9"},
    )
    assert r.status_code == 201
    return str(r.json()["tokens"]["access_token"])


async def test_l1_download_filters_to_last_hour(
    client: AsyncClient, tmp_path: Path, monkeypatch: Any
) -> None:
    log_path = tmp_path / "logs" / "gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    old_ts = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    new_ts = now.strftime("%Y-%m-%dT%H:%M:%S")
    log_path.write_text(
        f"{old_ts} OLD line that should be filtered\n{new_ts} FRESH line within window\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.adapters.hermes_cli.HermesCliAdapter.gateway_log_path",
        lambda self, profile=None: log_path,
    )

    token = await _bootstrap(client)
    r = await client.get(
        "/api/v1/bots/foo/logs/download?hours=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"] == 'attachment; filename="foo-gateway-1h.log"'
    body = r.text
    assert "FRESH line within window" in body
    assert "OLD line that should be filtered" not in body


async def test_l2_invalid_hours_returns_400(client: AsyncClient) -> None:
    token = await _bootstrap(client)
    r = await client.get(
        "/api/v1/bots/foo/logs/download?hours=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    # 2 is rejected by our explicit allowlist (even though 1<=2<=72 in the
    # Query annotation) — handler returns 400.
    assert r.status_code == 400, r.text


async def test_l2b_out_of_range_hours_rejected_by_query_validation(
    client: AsyncClient,
) -> None:
    token = await _bootstrap(client)
    r = await client.get(
        "/api/v1/bots/foo/logs/download?hours=999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text


async def test_l2c_download_redacts_secret_fingerprints(
    client: AsyncClient, tmp_path: Path, monkeypatch: Any
) -> None:
    log_path = tmp_path / "logs" / "gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    app_id = "cli_abcDEF1234567890"
    app_secret = "c" * 40
    log_path.write_text(
        f"{ts} INFO app_id={app_id} secret={app_secret}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.adapters.hermes_cli.HermesCliAdapter.gateway_log_path",
        lambda self, profile=None: log_path,
    )

    token = await _bootstrap(client)
    r = await client.get(
        "/api/v1/bots/foo/logs/download?hours=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert app_id not in r.text
    assert app_secret not in r.text
    assert "cli_****" in r.text
    assert "****" in r.text


async def test_l3_missing_log_returns_empty_body(
    client: AsyncClient, tmp_path: Path, monkeypatch: Any
) -> None:
    """Missing gateway.log → 200 with empty body (graceful fallback)."""
    monkeypatch.setattr(
        "app.adapters.hermes_cli.HermesCliAdapter.gateway_log_path",
        lambda self, profile=None: tmp_path / "nope" / "gateway.log",
    )
    token = await _bootstrap(client)
    r = await client.get(
        "/api/v1/bots/foo/logs/download?hours=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.text == ""


async def test_l_all_allowed_hours_succeed(
    client: AsyncClient, tmp_path: Path, monkeypatch: Any
) -> None:
    log_path = tmp_path / "logs" / "gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("nothing here\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.adapters.hermes_cli.HermesCliAdapter.gateway_log_path",
        lambda self, profile=None: log_path,
    )
    token = await _bootstrap(client)
    for h in (1, 6, 24, 72):
        r = await client.get(
            f"/api/v1/bots/foo/logs/download?hours={h}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, (h, r.text)
        assert r.headers["content-disposition"].endswith(f'filename="foo-gateway-{h}h.log"')
