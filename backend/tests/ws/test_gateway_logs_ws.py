"""WS endpoint integration tests — Phase 4 GATEWAY-05/06/08/09 (04-06).

Test plan (anchors per 04-06-PLAN.md <behavior>):

  WS1  — missing token            → close 1008
  WS2  — bad Origin               → close 1008
  WS3  — empty allowlist skip     → no Origin check
  WS4  — wrong audience           → close 1008
  WS5  — expired token            → close 1008
  WS6  — happy session frame      → first frame `{"type":"session", ...}`
  WS7  — subscribe registered     → hub has a subscriber after subscribe
  WS8  — subscribe timeout        → server sent session BEFORE 10s close
  WS9  — log_line delivery        → publish lands as log_line envelope
  WS10 — keyword filter           → non-matching publish is dropped
  WS11 — backpressure marker      → dropped_marker count >= 500 for 1500 lines
  WS12 — disconnect cleanup       → hub.unsubscribe leaves zero leaks
  WS13 — supervisor missing       → server sends `{"type":"error",...}` then closes

The tests are intentionally written as plain ``def`` (sync) functions because
Starlette's :class:`TestClient` is sync and runs the app on its own portal /
event loop. Mixing portal-managed ``WebSocketTestSession`` with our
async-mode test loop would create two competing event loops sharing the same
:class:`asyncio.Queue` instances — fertile ground for hangs. The trade-off is
that we use ``ws.portal.call(hub.publish, line)`` to schedule publishes on
the app's loop (which IS thread-safe).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.config import get_settings
from app.services.gateway.broadcast_hub import BroadcastHub, Subscriber
from app.ws import gateway_logs as ws_gateway_logs

# ---------------------------------------------------------------------------
# Stub supervisor + registry — keeps WS tests independent of the full
# SupervisorRegistry / dispatcher / LogTailer stack from 04-04. The endpoint
# only depends on ``app.state.supervisor_registry.get(bot_name).hub``.
# ---------------------------------------------------------------------------


class _StubSupervisor:
    """Minimal Supervisor stub: just exposes a real :class:`BroadcastHub`."""

    def __init__(self, hub: BroadcastHub) -> None:
        self.hub = hub


class _StubRegistry:
    """``.get(bot_name)`` → :class:`_StubSupervisor` or ``None``."""

    def __init__(self) -> None:
        self._supervisors: dict[str, _StubSupervisor] = {}

    def register(self, bot_name: str, hub: BroadcastHub) -> _StubSupervisor:
        sup = _StubSupervisor(hub)
        self._supervisors[bot_name] = sup
        return sup

    def get(self, bot_name: str) -> _StubSupervisor | None:
        return self._supervisors.get(bot_name)


class _FileCli:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path

    def gateway_log_path(self, profile: str | None = None) -> Path:
        return self.log_path


class _PreFillingHub(BroadcastHub):
    """BroadcastHub variant that pre-fills the subscriber queue on subscribe.

    Used only for WS11 (backpressure). Pre-filling happens on the app's loop
    inside the WS handler, so all ``put_nowait`` calls are loop-safe and the
    drop-newest counter wins exactly as in production with a fast publisher
    + slow consumer.
    """

    def __init__(self, prefill_count: int) -> None:
        super().__init__()
        self._prefill_count = prefill_count

    def subscribe(self, *, keywords: list[str], level_min: str | None) -> Subscriber:
        sub = super().subscribe(keywords=keywords, level_min=level_min)
        # Use the hub's own publish path so the drop counter increments
        # exactly as it would for a slow real subscriber (1000 cap, drop-newest).
        for i in range(self._prefill_count):
            self.publish(f"info: prefilled-line-{i}")
        return sub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test gets a clean Settings instance with a known JWT secret."""
    import app.config as c

    monkeypatch.setenv("HERMES_CONSOLE_JWT_SECRET", "ws-test-secret-at-least-32-chars-long-pad")
    monkeypatch.setenv("HERMES_CONSOLE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("HERMES_CONSOLE_MASTER_KEY_PATH", "/tmp/ws-test-master.key")
    # Default: empty allowlist (skip Origin check). Tests that need a non-empty
    # allowlist override via monkeypatch.setenv before reading settings.
    monkeypatch.delenv("HERMES_CONSOLE_WS_ALLOWED_ORIGINS", raising=False)
    c._settings = None
    yield
    c._settings = None


def _build_app(registry: _StubRegistry) -> FastAPI:
    """Build a tiny FastAPI app with just the WS router + a stub registry.

    No DB, no lifespan — the WS endpoint only reaches into
    ``app.state.supervisor_registry`` so we wire that explicitly.
    """
    app = FastAPI()
    app.state.supervisor_registry = registry
    app.include_router(ws_gateway_logs.router)
    return app


def _mint_token(
    bot_name: str,
    *,
    sub: str = "1",
    role: str = "Owner",
    ttl_seconds: int = 60,
    audience: str | None = None,
    now: datetime | None = None,
    token_type: str = "ws",
) -> str:
    """Mint a JWT with explicit knobs so we can craft each failure mode.

    We bypass :func:`encode_ws_token` for the negative tests so we can inject
    the wrong audience / wrong type / negative TTL without changing prod code.
    """
    settings = get_settings()
    if now is None:
        now = datetime.now(UTC)
    aud = audience if audience is not None else f"ws-gateway-logs:{bot_name}"
    payload: dict[str, Any] = {
        "sub": sub,
        "role": role,
        "type": token_type,
        "aud": aud,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


# ---------------------------------------------------------------------------
# WS1 — missing token
# ---------------------------------------------------------------------------


def test_ws1_missing_token_closes_1008() -> None:
    """WS1 — Connect with no `?token=` query param → server closes 1008."""
    registry = _StubRegistry()
    registry.register("foo", BroadcastHub())
    app = _build_app(registry)
    client = TestClient(app)
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/api/v1/ws/gateway/foo/logs"),
    ):
        pass
    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# WS2 — bad Origin
# ---------------------------------------------------------------------------


def test_ws2_bad_origin_closes_1008(monkeypatch: pytest.MonkeyPatch) -> None:
    """WS2 — Origin not in non-empty ws_allowed_origins → close 1008."""
    monkeypatch.setenv(
        "HERMES_CONSOLE_WS_ALLOWED_ORIGINS",
        '["http://allowed.example"]',
    )
    import app.config as c

    c._settings = None  # rebuild Settings with the new env

    registry = _StubRegistry()
    registry.register("foo", BroadcastHub())
    app = _build_app(registry)
    client = TestClient(app)
    from starlette.websockets import WebSocketDisconnect

    token = _mint_token("foo")
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(
            f"/api/v1/ws/gateway/foo/logs?token={token}",
            headers={"origin": "http://malicious.example"},
        ),
    ):
        pass
    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# WS3 — empty allowlist skips Origin check
# ---------------------------------------------------------------------------


def test_ws3_empty_allowlist_skips_origin_check() -> None:
    """WS3 — Empty ws_allowed_origins ⇒ Origin not validated."""
    registry = _StubRegistry()
    registry.register("foo", BroadcastHub())
    app = _build_app(registry)
    client = TestClient(app)

    token = _mint_token("foo")
    with client.websocket_connect(
        f"/api/v1/ws/gateway/foo/logs?token={token}",
        headers={"origin": "http://anything.invalid"},
    ) as ws:
        first = ws.receive_json()
        assert first["type"] == "session"


# ---------------------------------------------------------------------------
# WS4 — wrong audience
# ---------------------------------------------------------------------------


def test_ws4_wrong_audience_closes_1008() -> None:
    """WS4 — Token minted for `other` cannot read `foo` (cross-bot replay)."""
    registry = _StubRegistry()
    registry.register("foo", BroadcastHub())
    app = _build_app(registry)
    client = TestClient(app)
    from starlette.websockets import WebSocketDisconnect

    bad_token = _mint_token("foo", audience="ws-gateway-logs:other")
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={bad_token}"),
    ):
        pass
    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# WS5 — expired token
# ---------------------------------------------------------------------------


def test_ws5_expired_token_closes_1008() -> None:
    """WS5 — token with negative TTL (already expired) → close 1008."""
    registry = _StubRegistry()
    registry.register("foo", BroadcastHub())
    app = _build_app(registry)
    client = TestClient(app)
    from starlette.websockets import WebSocketDisconnect

    expired = _mint_token(
        "foo",
        ttl_seconds=60,
        now=datetime.now(UTC) - timedelta(seconds=120),
    )
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={expired}"),
    ):
        pass
    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# WS6 — happy path session frame
# ---------------------------------------------------------------------------


def test_ws6_session_frame_on_connect() -> None:
    """WS6 — valid token ⇒ first server frame is `{"type":"session",...}`."""
    registry = _StubRegistry()
    registry.register("foo", BroadcastHub())
    app = _build_app(registry)
    client = TestClient(app)

    token = _mint_token("foo")
    with client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "session"
        assert isinstance(first["session_token"], str)
        assert len(first["session_token"]) > 0


# ---------------------------------------------------------------------------
# WS7 — subscribe registers a subscriber on the hub
# ---------------------------------------------------------------------------


def test_ws7_subscribe_registers_with_hub() -> None:
    """WS7 — after the client sends `subscribe`, hub has 1 subscriber."""
    registry = _StubRegistry()
    hub = BroadcastHub()
    registry.register("foo", hub)
    app = _build_app(registry)
    client = TestClient(app)

    token = _mint_token("foo")
    with client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={token}") as ws:
        ws.receive_json()  # session
        ws.send_json({"type": "subscribe", "keywords": [], "level_min": "info"})
        # Bounce a publish through to confirm the subscribe completed (the
        # server must be in the read-loop before publish lands).
        ws.portal.call(hub.publish, "info: ping")
        msg = ws.receive_json()
        assert msg["type"] == "log_line"
        assert hub.subscriber_count == 1


# ---------------------------------------------------------------------------
# WS8 — MAJOR 7 ordering: session frame BEFORE 10s subscribe timeout
# ---------------------------------------------------------------------------


def test_subscribe_timeout_after_session_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WS8 — Server sends `session` frame BEFORE the subscribe-timeout fires.

    Verifies the ordering contract pinned by MAJOR 7 of 04-06: a slow client
    that takes the full 10s without sending the `subscribe` frame still sees
    the server's `session` frame first, THEN gets disconnected with 1008.

    To keep the test fast (we don't actually want to wait 10s), we
    monkeypatch ``FIRST_FRAME_TIMEOUT_SEC`` to a small value. The intent of
    the contract — session-before-timeout — is preserved.
    """
    monkeypatch.setattr(ws_gateway_logs, "FIRST_FRAME_TIMEOUT_SEC", 0.5)

    registry = _StubRegistry()
    registry.register("foo", BroadcastHub())
    app = _build_app(registry)
    client = TestClient(app)
    from starlette.websockets import WebSocketDisconnect

    token = _mint_token("foo")
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={token}") as ws,
    ):
        # (1) Session frame MUST arrive first.
        first = ws.receive_json()
        assert first["type"] == "session"
        # (2) Do NOT send subscribe — let the timeout fire.
        # (3) Next receive raises WebSocketDisconnect with 1008.
        ws.receive_json()
    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# WS9 — log_line delivery
# ---------------------------------------------------------------------------


def test_ws9_log_line_delivered_after_subscribe() -> None:
    """WS9 — publish('foo error: bar') ⇒ client receives log_line envelope."""
    registry = _StubRegistry()
    hub = BroadcastHub()
    registry.register("foo", hub)
    app = _build_app(registry)
    client = TestClient(app)

    token = _mint_token("foo")
    with client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={token}") as ws:
        ws.receive_json()  # session
        ws.send_json({"type": "subscribe", "keywords": ["error"], "level_min": "info"})
        ws.portal.call(hub.publish, "foo error: bar")
        msg = ws.receive_json()
        assert msg["type"] == "log_line"
        assert msg["text"] == "foo error: bar"
        assert msg["level"] == "error"
        assert isinstance(msg["ts"], str)


# ---------------------------------------------------------------------------
# WS10 — keyword filter excludes non-match
# ---------------------------------------------------------------------------


def test_ws10_keyword_filter_excludes_non_match() -> None:
    """WS10 — publish('info: ok') with keyword='error' filter ⇒ no delivery."""
    registry = _StubRegistry()
    hub = BroadcastHub()
    registry.register("foo", hub)
    app = _build_app(registry)
    client = TestClient(app)

    token = _mint_token("foo")
    with client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={token}") as ws:
        ws.receive_json()  # session
        ws.send_json({"type": "subscribe", "keywords": ["error"], "level_min": "info"})
        # Non-matching publish — should be silently dropped server-side.
        ws.portal.call(hub.publish, "info: ok")
        # Matching publish — should arrive.
        ws.portal.call(hub.publish, "error: matched")
        msg = ws.receive_json()
        assert msg["type"] == "log_line"
        assert msg["text"] == "error: matched"


# ---------------------------------------------------------------------------
# WS11 — backpressure dropped_marker
# ---------------------------------------------------------------------------


def test_ws11_backpressure_dropped_marker() -> None:
    """WS11 — 1500 pre-filled lines ⇒ dropped_marker count >= 500."""
    registry = _StubRegistry()
    hub = _PreFillingHub(prefill_count=1500)
    registry.register("foo", hub)
    app = _build_app(registry)
    client = TestClient(app)

    token = _mint_token("foo")
    log_lines = 0
    dropped_total = 0
    with client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={token}") as ws:
        ws.receive_json()  # session
        ws.send_json({"type": "subscribe", "keywords": [], "level_min": "info"})
        # Drain everything the server has queued — there are at most 1000
        # log_line frames + at least one dropped_marker (>=500 dropped).
        # We bound the loop at 1100 to defend against any infinite-frame bug.
        for _ in range(1100):
            msg = ws.receive_json()
            if msg["type"] == "log_line":
                log_lines += 1
            elif msg["type"] == "dropped_marker":
                dropped_total += int(msg["count"])
            if log_lines >= 1000 and dropped_total >= 500:
                break

    assert log_lines <= 1000, "subscriber queue must cap at 1000 (D-07)"
    assert dropped_total >= 500, (
        f"backpressure must surface drops as dropped_marker; got {dropped_total}"
    )


# ---------------------------------------------------------------------------
# WS12 — disconnect cleanup (no leaked subscriber)
# ---------------------------------------------------------------------------


def test_ws12_disconnect_unsubscribes_from_hub() -> None:
    """WS12 — closing the WS leaves zero subscribers on the hub."""
    registry = _StubRegistry()
    hub = BroadcastHub()
    registry.register("foo", hub)
    app = _build_app(registry)
    client = TestClient(app)

    token = _mint_token("foo")
    with client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={token}") as ws:
        ws.receive_json()  # session
        ws.send_json({"type": "subscribe", "keywords": [], "level_min": "info"})
        # Round-trip a publish so we know the handler reached the read loop
        # (the unsubscribe in the `finally` only fires after subscribe lands).
        ws.portal.call(hub.publish, "info: ping")
        ws.receive_json()  # log_line
    # ``with`` exit triggers WebSocketDisconnect on the server. The handler's
    # finally block must have called hub.unsubscribe — verify no leak.
    assert hub.subscriber_count == 0


# ---------------------------------------------------------------------------
# WS13 — supervisor missing for the bot
# ---------------------------------------------------------------------------


def test_ws13_no_supervisor_sends_error_then_closes() -> None:
    """WS13 — bot has no Supervisor (e.g. wizard not finished) → error frame."""
    registry = _StubRegistry()
    # Deliberately do NOT register "foo" — registry.get("foo") returns None.
    app = _build_app(registry)
    client = TestClient(app)
    from starlette.websockets import WebSocketDisconnect

    token = _mint_token("foo")
    with client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={token}") as ws:
        ws.receive_json()  # session
        ws.send_json({"type": "subscribe", "keywords": [], "level_min": "info"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "supervisor not running" in msg["msg"]
        # Next receive must raise WebSocketDisconnect (server closed).
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


# ---------------------------------------------------------------------------
# WS14 — profile log file history is visible immediately
# ---------------------------------------------------------------------------


def test_ws14_replays_recent_profile_log_before_live_hub_lines(tmp_path: Path) -> None:
    """Profile-scoped gateway.log history is visible immediately on connect."""
    log_path = tmp_path / "profiles" / "foo" / "logs" / "gateway.log"
    log_path.parent.mkdir(parents=True)
    app_id = "cli_abcDEF1234567890"
    log_path.write_text(
        f"2026-05-05 09:20:22 INFO existing profile log app_id={app_id}\n",
        encoding="utf-8",
    )

    registry = _StubRegistry()
    registry.register("foo", _PreFillingHub(prefill_count=1))
    app = _build_app(registry)
    app.state.cli = _FileCli(log_path)
    client = TestClient(app)

    token = _mint_token("foo")
    with client.websocket_connect(f"/api/v1/ws/gateway/foo/logs?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "session"
        ws.send_json({"type": "subscribe", "keywords": [], "level_min": "info"})
        msg = ws.receive_json()
        assert msg["type"] == "log_line"
        assert "existing profile log" in msg["text"]
        assert app_id not in msg["text"]
        assert "cli_****" in msg["text"]
