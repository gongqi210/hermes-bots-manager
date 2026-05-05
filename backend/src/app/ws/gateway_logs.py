"""Gateway log streaming WebSocket — Phase 4 GATEWAY-05/06/08/09.

Endpoint: ``/api/v1/ws/gateway/{bot_name}/logs``

Wire protocol (envelope schemas — frontend log viewer must match exactly):

  Server → Client (first frame, after WS upgrade):
      {"type": "session", "session_token": "<jwt access>"}

  Client → Server (mandatory first frame within 10s of session frame):
      {"type": "subscribe", "keywords": ["error", "feishu"], "level_min": "info"}

  Server → Client (continuous; one envelope per tail line):
      {"type": "log_line", "ts": "2026-05-04T02:30:01+00:00", "level": "error",
       "text": "<raw line text>"}

  Server → Client (drop counter; emitted lazily before the next log_line when
  the per-connection bounded queue dropped lines since the last marker):
      {"type": "dropped_marker", "count": 17}

  Server → Client (Supervisor not registered — bot configured but no .env yet,
  or an unknown bot name was given). Sent before close():
      {"type": "error", "msg": "supervisor not running"}

Auth + Origin contract (GATEWAY-08):
  * 60s TTL JWT in ``?token=<jwt>`` query param.
  * Audience claim must equal ``ws-gateway-logs:{bot_name}`` (cross-bot replay
    protection — see ``app.auth.jwt_utils.decode_ws_token``).
  * If ``settings.ws_allowed_origins`` is non-empty, the ``Origin`` header MUST
    be on the list. Empty list = skip (tests + same-origin dev).

Backpressure (GATEWAY-09 / D-07):
  * The :class:`BroadcastHub` per-Subscriber queue caps at 1000 lines with
    drop-newest semantics; ``Subscriber.dropped_count`` increments per drop.
  * This handler emits a ``dropped_marker`` envelope BEFORE the next
    ``log_line`` whenever the cumulative dropped count has grown — the UI
    can render a "已丢弃 N 行" banner without polling.

Disconnect cleanup:
  The handler ALWAYS calls ``supervisor.hub.unsubscribe(sub)`` in a
  ``finally`` block (subscribers leak otherwise; the hub still has the
  bounded queue holding 1000 lines worth of memory per leaked sub).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import aiofiles
import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.adapters.log_tail import LogTailer
from app.auth.jwt_utils import decode_ws_token, encode_access_token
from app.config import get_settings
from app.services.gateway.broadcast_hub import _matches as log_line_matches

router = APIRouter()
logger = logging.getLogger(__name__)

# 10s gives a slow browser plenty of time to send the subscribe frame after
# rendering the log viewer; longer is wasteful (idle connections hold sockets);
# shorter risks racing slow tabs. See acceptance criterion: literal preserved.
FIRST_FRAME_TIMEOUT_SEC = 10

# Recognise common Hermes / lark-oapi log levels embedded in the raw line.
# Case-insensitive — Hermes uses upper, lark-oapi often lower.
_LOG_LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL)\b", re.IGNORECASE)
_RECENT_LOG_LINES = 500


def _parse_level(line: str) -> str:
    """Best-effort level extraction; default 'info' for missing-level lines."""
    m = _LOG_LEVEL_RE.search(line)
    if m is None:
        return "info"
    token = m.group(1).lower()
    if token == "warning":
        token = "warn"
    return token


async def _tail_recent_lines(path: Path, n: int = _RECENT_LOG_LINES) -> list[str]:
    """Return the last ``n`` lines from ``path``; ``[]`` if the log is absent."""
    try:
        async with aiofiles.open(path, "rb") as f:
            await f.seek(0, 2)
            size = await f.tell()
            read_from = max(0, size - 256 * 1024)
            await f.seek(read_from)
            data = await f.read()
    except FileNotFoundError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-n:]


async def _send_log_line(websocket: WebSocket, line: str) -> None:
    await websocket.send_json(
        {
            "type": "log_line",
            "ts": datetime.now(UTC).isoformat(),
            "level": _parse_level(line),
            "text": line,
        }
    )


async def _stream_log_file(
    websocket: WebSocket,
    *,
    log_path: Path,
    keywords: list[str],
    level_min: str | None,
) -> None:
    """Replay recent lines from ``log_path`` and then follow appends directly."""
    for line in await _tail_recent_lines(log_path):
        if log_line_matches(line, keywords, level_min):
            await _send_log_line(websocket, line)

    tailer = LogTailer(log_path, start_at_end=log_path.exists())
    tailer_task = asyncio.create_task(tailer.run(), name=f"WSLogTailer[{log_path.name}]")
    try:
        while True:
            line = await tailer.queue.get()
            if log_line_matches(line, keywords, level_min):
                await _send_log_line(websocket, line)
    finally:
        tailer.stop()
        tailer_task.cancel()
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(tailer_task, timeout=2.0)


@router.websocket("/api/v1/ws/gateway/{bot_name}/logs")
async def gateway_logs_ws(websocket: WebSocket, bot_name: str) -> None:
    """Stream a Bot's gateway log to one WS client.

    Lifecycle:
      1. Origin check (skip if ``ws_allowed_origins`` is empty).
      2. ``?token`` param check + ``decode_ws_token(audience scoped)``.
      3. ``websocket.accept()`` → send ``session`` frame.
      4. Wait up to 10s for the client's mandatory ``subscribe`` frame.
      5. Resolve Supervisor on ``app.state.supervisor_registry``.
      6. ``hub.subscribe(...)`` → consume queue forever, fan out as ``log_line``
         envelopes; emit ``dropped_marker`` lazily when the queue overflowed.
      7. ``finally:`` ``hub.unsubscribe(sub)`` — never leaks subscribers.
    """
    settings = get_settings()

    # 1. Origin check — Pitfall #3: native WebSocket clients (curl, asyncio
    # tests) often omit the Origin header; an empty allowlist skips the check
    # entirely so dev + tests work without configuration. Production should
    # set ws_allowed_origins to the console URL(s).
    if settings.ws_allowed_origins:
        origin = websocket.headers.get("origin")
        if origin not in settings.ws_allowed_origins:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    # 2. Token check (60s TTL, audience-scoped — see decode_ws_token).
    token = websocket.query_params.get("token")
    if not token:
        # No token present at all (browser forgot to call /api/v1/ws-token).
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        payload = decode_ws_token(token, bot_name=bot_name)
    except jwt.InvalidTokenError:
        # Covers expired / invalid signature / wrong audience / wrong type.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 3. Accept the upgrade. From here on the protocol is JSON envelopes.
    await websocket.accept()
    # Mint a regular access token so any subsequent REST calls the client
    # makes during this WS session can use the standard Bearer flow.
    session_token, _ = encode_access_token(sub=str(payload["sub"]), role=str(payload["role"]))
    await websocket.send_json({"type": "session", "session_token": session_token})

    # 4. First-frame subscribe (mandatory, within FIRST_FRAME_TIMEOUT_SEC).
    # MAJOR 7 ordering: the session frame above is sent BEFORE this wait,
    # so a slow client that takes the full 10s still sees the session frame.
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=FIRST_FRAME_TIMEOUT_SEC)
    except TimeoutError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    except (json.JSONDecodeError, WebSocketDisconnect):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if not isinstance(first, dict) or first.get("type") != "subscribe":
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    keywords = list(first.get("keywords", []) or [])
    level_min = first.get("level_min")

    # 5. Resolve Supervisor on app.state. The Supervisor was wired by the
    # FastAPI lifespan in app.main; tests inject a stub registry directly.
    registry = websocket.app.state.supervisor_registry
    supervisor = registry.get(bot_name)
    if supervisor is None:
        # Bot exists in the URL path but no Supervisor — typical when the
        # Wizard hasn't finished writing the .env yet, or the bot_name is
        # unknown. We send a structured error before close so the UI can
        # surface a friendly Chinese message instead of a generic close.
        await websocket.send_json({"type": "error", "msg": "supervisor not running"})
        await websocket.close()
        return

    cli = getattr(websocket.app.state, "cli", None)
    if cli is not None:
        try:
            await _stream_log_file(
                websocket,
                log_path=cli.gateway_log_path(bot_name),
                keywords=keywords,
                level_min=level_min,
            )
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("WS file-log handler error for bot=%s", bot_name)
            with contextlib.suppress(Exception):
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    sub = supervisor.hub.subscribe(keywords=keywords, level_min=level_min)
    last_dropped = 0
    try:
        while True:
            # Block until a line arrives (or the client disconnects, which
            # surfaces via WebSocketDisconnect on the next send_json below).
            line = await sub.queue.get()

            # Backpressure surfacing: if the hub dropped lines since our last
            # marker, emit a dropped_marker envelope first so the UI can
            # render the banner BEFORE the next log_line. D-07 anchor.
            if sub.dropped_count > last_dropped:
                await websocket.send_json(
                    {
                        "type": "dropped_marker",
                        "count": sub.dropped_count - last_dropped,
                    }
                )
                last_dropped = sub.dropped_count

            await websocket.send_json(
                {
                    "type": "log_line",
                    "ts": datetime.now(UTC).isoformat(),
                    "level": _parse_level(line),
                    "text": line,
                }
            )
    except WebSocketDisconnect:
        # Normal client-initiated disconnect.
        pass
    except Exception:
        # Anything else is a server-side bug; close 1011 so the client
        # reconnects (partysocket exponential backoff handles it).
        logger.exception("WS handler error for bot=%s", bot_name)
        with contextlib.suppress(Exception):
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    finally:
        # MUST run even on exception — leaked subscribers hold a 1000-slot
        # queue forever and silently corrupt the hub's fan-out fairness.
        supervisor.hub.unsubscribe(sub)
