from __future__ import annotations

from contextvars import ContextVar

current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)
client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)
