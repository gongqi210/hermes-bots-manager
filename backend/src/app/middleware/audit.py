from __future__ import annotations

import logging
from datetime import UTC, datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.db.session import get_sessionmaker
from app.middleware.context import client_ip, current_user_id
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class AuditMiddleware:
    """Pure ASGI middleware logging every mutating request (success or failure) to audit_log.

    Written as pure ASGI (not BaseHTTPMiddleware) so the audit write runs *after*
    the endpoint's get_session dependency has released its connection. Starlette's
    BaseHTTPMiddleware returns from `call_next` before route cleanup, which caused
    `database is locked` when both transactions hit SQLite concurrently.

    Simple version per user decision — no event bus, no decorators.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"].upper()
        path = scope.get("path", "")[:512]

        # Populate client_ip ContextVar for any downstream code.
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        client = scope.get("client")
        ip = headers.get("x-forwarded-for") or (client[0] if client else None)
        client_ip.set(ip)

        if method not in _MUTATING:
            await self.app(scope, receive, send)
            return

        status_holder: dict[str, int] = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        error_msg: str | None = None
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            # Continue to write the failure row before re-raising.
            status_code = status_holder["status"]
            result = "failure"
            await _write_audit_row(
                method=method,
                path=path,
                result=result,
                actor_id=current_user_id.get(),
                actor_ip=client_ip.get(),
                error=error_msg,
            )
            raise
        else:
            status_code = status_holder["status"]
            result = "success" if 200 <= status_code < 400 else "failure"
            # At this point self.app has fully returned, meaning the route's
            # get_session dependency has committed and released its connection —
            # so this write is safe (no SQLITE_BUSY on WAL).
            await _write_audit_row(
                method=method,
                path=path,
                result=result,
                actor_id=current_user_id.get(),
                actor_ip=client_ip.get(),
                error=None,
            )


async def _write_audit_row(
    *,
    method: str,
    path: str,
    result: str,
    actor_id: int | None,
    actor_ip: str | None,
    error: str | None,
) -> None:
    try:
        maker = get_sessionmaker()
        async with maker() as session:
            session.add(
                AuditLog(
                    actor_id=actor_id,
                    actor_ip=actor_ip,
                    method=method,
                    path=path,
                    result=result,
                    error=(error[:2000] if error else None),
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Failed to write audit_log row for %s %s", method, path)
