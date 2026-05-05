"""Onboarding REST API integration tests — D-19."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboarding_run import OnboardingRun, OnboardingStatus


@pytest_asyncio.fixture
async def app(engine: Any) -> FastAPI:
    from app.main import create_app

    return create_app()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _bootstrap_owner(client: AsyncClient) -> tuple[str, int]:
    r = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "ownerpw9"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["tokens"]["access_token"], body["user"]["id"]


async def _login_as(client: AsyncClient, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["tokens"]["access_token"])


async def test_o1_login_creates_in_progress_run(client: AsyncClient, session: AsyncSession) -> None:
    """The /auth/login hook creates an in_progress OnboardingRun row."""
    _token, user_id = await _bootstrap_owner(client)
    # Bootstrap already authenticated — issue a fresh /login to fire the hook.
    await _login_as(client, "owner", "ownerpw9")
    rows = (
        (await session.execute(select(OnboardingRun).where(OnboardingRun.user_id == user_id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == OnboardingStatus.IN_PROGRESS.value
    assert rows[0].login_at is not None
    assert rows[0].last_step == "login"


async def test_o2_get_runs_returns_recent_runs_for_current_user(
    client: AsyncClient, session: AsyncSession
) -> None:
    token, user_id = await _bootstrap_owner(client)
    # Trigger one login → one run.
    await _login_as(client, "owner", "ownerpw9")
    r = await client.get("/api/v1/onboarding/runs", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["user_id"] == user_id
    assert body[0]["status"] == "in_progress"


async def test_o3_mark_message_received_writes_first_message_at_and_success(
    client: AsyncClient, session: AsyncSession
) -> None:
    token, user_id = await _bootstrap_owner(client)
    await _login_as(client, "owner", "ownerpw9")
    rows = (
        (await session.execute(select(OnboardingRun).where(OnboardingRun.user_id == user_id)))
        .scalars()
        .all()
    )
    run_id = rows[0].id

    r = await client.post(
        f"/api/v1/onboarding/{run_id}/mark-message-received",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"

    # Fresh read to verify the row was updated. The handler's session committed
    # its changes; with expire_on_commit=False our test session keeps the
    # pre-update copy in its identity map, so we expire to force a re-read.
    session.expire_all()
    refreshed = (
        await session.execute(select(OnboardingRun).where(OnboardingRun.id == run_id))
    ).scalar_one()
    assert refreshed.first_message_at is not None
    assert refreshed.status == OnboardingStatus.SUCCESS.value
    assert refreshed.total_duration_ms is not None
    assert refreshed.total_duration_ms >= 0


async def test_o4_mark_message_received_404_when_run_not_owned(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A user cannot mark another user's run."""
    owner_token, owner_id = await _bootstrap_owner(client)
    # Owner creates a Viewer.
    r = await client.post(
        "/api/v1/auth/users",
        json={"username": "viewer1", "password": "viewerpw9", "role": "Viewer"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r.status_code == 201, r.text
    # Owner logs in to create their run.
    await _login_as(client, "owner", "ownerpw9")
    rows = (
        (await session.execute(select(OnboardingRun).where(OnboardingRun.user_id == owner_id)))
        .scalars()
        .all()
    )
    owner_run_id = rows[0].id

    # Viewer logs in (creates own run) and tries to mark Owner's run.
    viewer_token = await _login_as(client, "viewer1", "viewerpw9")
    r2 = await client.post(
        f"/api/v1/onboarding/{owner_run_id}/mark-message-received",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r2.status_code == 404, r2.text
