from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


@pytest_asyncio.fixture
async def app(engine: Any) -> FastAPI:
    from app.main import create_app

    return create_app()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_audit_written_on_success_and_failure(
    client: AsyncClient, session: AsyncSession
) -> None:
    # Success path: bootstrap POST → one success row.
    r = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "hello12345"},
    )
    assert r.status_code == 201, r.text

    # Failure path: bad login → one failure row.
    r2 = await client.post("/api/v1/auth/login", json={"username": "owner", "password": "WRONG123"})
    assert r2.status_code == 401

    # GET /health (non-mutating) must NOT be audited.
    await client.get("/api/v1/health")

    rows = (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].method == "POST"
    assert rows[0].path == "/api/v1/auth/bootstrap"
    assert rows[0].result == "success"
    assert rows[1].method == "POST"
    assert rows[1].path == "/api/v1/auth/login"
    assert rows[1].result == "failure"
    for row in rows:
        assert row.created_at is not None


async def test_concurrent_writes_no_locked_error(
    client: AsyncClient, session: AsyncSession
) -> None:
    # Pitfall #7 verification: 10 concurrent POSTs must not produce "database is locked".
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner", "password": "hello12345"},
    )

    async def one_call(i: int) -> int:
        r = await client.post(
            "/api/v1/auth/login",
            json={"username": f"user{i}", "password": "doesntmatter"},
        )
        return r.status_code

    statuses = await asyncio.gather(*(one_call(i) for i in range(10)))
    # all unknown user → 401, no 500
    assert all(s == 401 for s in statuses), statuses
    count = await session.scalar(select(func.count(AuditLog.id)))
    # 1 (bootstrap success) + 10 (login failures) = 11 rows.
    assert count == 11
