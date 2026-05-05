"""Pydantic schemas for the Phase 4 Gateway REST + WebSocket auth APIs.

The :data:`GatewayState` literal is the single source of truth for the 5-state
status vocabulary (CONTEXT D-17 / D-18); the Bot ORM column
``gateway_state_cache`` and ``status_decider`` MUST agree on these values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# 5-state vocabulary shared between status_decider, Supervisor, and the
# ORM column ``Bot.gateway_state_cache``. Keep this in lock-step with both.
GatewayState = Literal["running", "starting", "stopped", "error", "unconfigured"]


class GatewayStatusOut(BaseModel):
    """Response body for ``GET /api/v1/bots/{name}/gateway/status`` (GATEWAY-01)."""

    bot_name: str
    state: GatewayState
    why: str  # 中文 human-readable reason — D-17 / D-18 vocabulary
    last_state_changed_at: datetime | None
    pid: int | None
    active_profile: str | None  # which profile owns the singleton gateway today
    is_active_profile: bool  # True iff this bot's profile == active_profile


class GatewayActionRequest(BaseModel):
    """Body for ``POST /api/v1/bots/{name}/gateway/{action}``.

    Today the action is encoded in the URL path so this body is empty —
    reserved for future fields (force, profile-switch hint, …).
    """


class GatewayActionResponse(BaseModel):
    """Response body for ``POST /api/v1/bots/{name}/gateway/{action}``."""

    bot_name: str
    action: Literal["start", "stop", "restart"]
    new_state: GatewayState
    recent_log_tail: list[str]  # last 200 lines (GATEWAY-02 浮层)


class WSTokenRequest(BaseModel):
    """Body for ``POST /api/v1/bots/{name}/gateway/logs/ws-token``."""

    bot_name: str


class WSTokenResponse(BaseModel):
    """One-shot 60-second WS bearer token for the gateway-log stream."""

    token: str
    expires_in: int  # seconds; always 60


class AllowlistOut(BaseModel):
    """Response body for ``GET /api/v1/bots/{name}/allowlist``."""

    bot_name: str
    users: list[str]  # ou_xxx OpenIDs (CONTEXT FINDING-04: comma-separated)


class AllowlistUpdateIn(BaseModel):
    """Body for ``PUT /api/v1/bots/{name}/allowlist``."""

    users: list[str]
