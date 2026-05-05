"""Pairings REST router integration tests — Phase 4."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import CliResult, HermesCliAdapter, ProfileFsAdapter
from app.models.bot import Bot
from app.models.pairing import Pairing, PairingStatus
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


def _seed_pairing(
    bot_id: int,
    *,
    code: str = "abc123XYZ",
    status_value: str = PairingStatus.PENDING.value,
    expires_in_minutes: int = 9,
) -> Pairing:
    now = datetime.now(UTC)
    return Pairing(
        bot_id=bot_id,
        platform="feishu",
        code_plaintext=code,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        code_last4=code[-4:],
        feishu_user_id=None,
        status=status_value,
        intercepted_at=now,
        expires_at=now + timedelta(minutes=expires_in_minutes),
    )


# ----------------------------------------------------------------------
# P1-P3 — list pairings.
# ----------------------------------------------------------------------


async def test_p0_get_pairings_syncs_pending_from_hermes_cli(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    bot = Bot(name="foo", tags=[])
    session.add(bot)
    await session.commit()
    fake_host.queue_response(
        ["-p", "foo", "pairing", "list"],
        CliResult(
            0,
            """
  Pending Pairing Requests (1):
  Platform     Code       User ID              Name                 Age
  --------     ----       -------              ----                 ---
  feishu       FZ439THC   ou_fixturepending00000001                      2m ago

  No approved users.
""",
            "",
        ),
    )

    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/pairings", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["bot_name"] == "foo"
    assert body[0]["code_last4"] == "9THC"
    assert body[0]["feishu_user_id"] == "ou_fixturepending00000001"
    assert body[0]["seconds_to_expiry"] <= 8 * 60


async def test_p0_get_pairings_skips_hermes_rows_already_past_ttl(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    bot = Bot(name="foo", tags=[])
    session.add(bot)
    await session.commit()
    fake_host.queue_response(
        ["-p", "foo", "pairing", "list"],
        CliResult(
            0,
            """
  Pending Pairing Requests (1):
  Platform     Code       User ID              Name                 Age
  --------     ----       -------              ----                 ---
  feishu       OLD9THC    ou_fixturepending00000001                      15m ago

  No approved users.
""",
            "",
        ),
    )

    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/pairings", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    assert r.json() == []
    rows = (await session.execute(select(Pairing))).scalars().all()
    assert rows == []


async def test_p1_get_pairings_lists_pending_with_seconds_to_expiry(
    client: AsyncClient, session: AsyncSession
) -> None:
    bot = Bot(name="foo", tags=[])
    session.add(bot)
    await session.flush()
    session.add_all([_seed_pairing(bot.id, code="a1b2c3d4")])
    await session.commit()

    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/pairings", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["bot_name"] == "foo"
    assert body[0]["code_last4"] == "c3d4"
    assert "code_plaintext" not in body[0]
    assert body[0]["seconds_to_expiry"] >= 0


async def test_p2_get_pairings_filter_by_bot_name(
    client: AsyncClient, session: AsyncSession
) -> None:
    foo = Bot(name="foo", tags=[])
    bar = Bot(name="bar", tags=[])
    session.add_all([foo, bar])
    await session.flush()
    session.add_all(
        [
            _seed_pairing(foo.id, code="foofoo01"),
            _seed_pairing(bar.id, code="barbar02"),
        ]
    )
    await session.commit()

    token = await _bootstrap_owner(client)
    r = await client.get(
        "/api/v1/pairings?bot_name=bar",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert [p["bot_name"] for p in r.json()] == ["bar"]


async def test_p3_get_pairings_forbidden_for_editor_and_viewer(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner_token = await _bootstrap_owner(client)
    editor_token = await _make_user(client, owner_token, "editor1", "Editor")
    r = await client.get("/api/v1/pairings", headers={"Authorization": f"Bearer {editor_token}"})
    assert r.status_code == 403, r.text
    viewer_token = await _make_user(client, owner_token, "viewer1", "Viewer")
    r = await client.get("/api/v1/pairings", headers={"Authorization": f"Bearer {viewer_token}"})
    assert r.status_code == 403


# ----------------------------------------------------------------------
# P4-P7 — approve / reject.
# ----------------------------------------------------------------------


async def test_p4_approve_calls_hermes_clears_plaintext_returns_200(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    bot = Bot(name="foo", tags=[])
    session.add(bot)
    await session.flush()
    pairing = _seed_pairing(bot.id, code="approveit")
    session.add(pairing)
    await session.commit()
    pairing_id = pairing.id

    fake_host.active_profile = "foo"
    fake_host.queue_response(
        ["-p", "foo", "pairing", "approve", "feishu", "approveit"],
        CliResult(0, "Approved\n", ""),
    )

    token = await _bootstrap_owner(client)
    r = await client.post(
        f"/api/v1/pairings/{pairing_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"

    session.expire_all()
    refreshed = (
        await session.execute(select(Pairing).where(Pairing.id == pairing_id))
    ).scalar_one()
    assert refreshed.status == PairingStatus.APPROVED.value
    assert refreshed.code_plaintext is None  # GATEWAY-12
    assert refreshed.processed_at is not None
    assert refreshed.processed_by_user_id is not None


async def test_p5_approve_when_pairing_already_expired_returns_410(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    bot = Bot(name="foo", tags=[])
    session.add(bot)
    await session.flush()
    pairing = _seed_pairing(bot.id, code="staleone", expires_in_minutes=-1)
    session.add(pairing)
    await session.commit()
    pairing_id = pairing.id
    fake_host.active_profile = "foo"

    token = await _bootstrap_owner(client)
    r = await client.post(
        f"/api/v1/pairings/{pairing_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 410, r.text

    session.expire_all()
    refreshed = (
        await session.execute(select(Pairing).where(Pairing.id == pairing_id))
    ).scalar_one()
    assert refreshed.status == PairingStatus.EXPIRED.value
    assert refreshed.code_plaintext is None  # GATEWAY-12 expire-on-fly


async def test_p6_approve_when_hermes_says_expired_marks_expired_and_returns_410(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    bot = Bot(name="foo", tags=[])
    session.add(bot)
    await session.flush()
    pairing = _seed_pairing(bot.id, code="hermesxp")
    session.add(pairing)
    await session.commit()
    pairing_id = pairing.id

    fake_host.active_profile = "foo"
    # FINDING-05: hermes pairing approve prints "not found or expired" with
    # exit-code 0; the adapter classifies as pairing_expired regardless.
    fake_host.queue_response(
        ["-p", "foo", "pairing", "approve", "feishu", "hermesxp"],
        CliResult(0, "Code 'hermesxp' not found or expired\n", ""),
    )

    token = await _bootstrap_owner(client)
    r = await client.post(
        f"/api/v1/pairings/{pairing_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 410, r.text
    session.expire_all()
    refreshed = (
        await session.execute(select(Pairing).where(Pairing.id == pairing_id))
    ).scalar_one()
    assert refreshed.status == PairingStatus.EXPIRED.value
    assert refreshed.code_plaintext is None


async def test_p7_reject_is_db_only_does_not_call_hermes(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    bot = Bot(name="foo", tags=[])
    session.add(bot)
    await session.flush()
    pairing = _seed_pairing(bot.id, code="rejectme")
    session.add(pairing)
    await session.commit()
    pairing_id = pairing.id

    token = await _bootstrap_owner(client)
    r = await client.post(
        f"/api/v1/pairings/{pairing_id}/reject",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"

    # No hermes invocation occurred.
    assert all("pairing" not in c[0] for c in fake_host.calls), fake_host.calls

    session.expire_all()
    refreshed = (
        await session.execute(select(Pairing).where(Pairing.id == pairing_id))
    ).scalar_one()
    assert refreshed.status == PairingStatus.REJECTED.value
    assert refreshed.code_plaintext is None


# ----------------------------------------------------------------------
# P8 — profile-scoped approve.
# ----------------------------------------------------------------------


async def test_p8_approve_uses_profile_flag_even_when_active_profile_differs(
    client: AsyncClient,
    fake_host: InMemoryHostOps,
    session: AsyncSession,
) -> None:
    """The adapter invokes ``hermes -p <bot> pairing approve``; the REST layer
    must not block on the global active profile because profile-scoped Hermes
    commands work while another profile is marked active.
    """
    bot = Bot(name="foo", tags=[])
    session.add(bot)
    await session.flush()
    pairing = _seed_pairing(bot.id, code="codeone")
    session.add(pairing)
    await session.commit()
    pairing_id = pairing.id
    fake_host.active_profile = "bar"  # mismatch
    fake_host.queue_response(
        ["-p", "foo", "pairing", "approve", "feishu", "codeone"],
        CliResult(0, "Approved\n", ""),
    )

    token = await _bootstrap_owner(client)
    r = await client.post(
        f"/api/v1/pairings/{pairing_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert ["-p", "foo", "pairing", "approve", "feishu", "codeone"] in [
        c[0] for c in fake_host.calls
    ]
