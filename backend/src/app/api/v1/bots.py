"""Bot REST endpoints — BOT-01..09.

Audit middleware (Phase 1, pure-ASGI) automatically logs every POST/PATCH/
DELETE here. No new audit code needed.

Hermes CLI hint → HTTP status mapping (centralized so 02-05 / 02-06 don't
need to duplicate it):
    * duplicate         → 409
    * invalid_name      → 422 (Pydantic catches first; CLI fallback)
    * reserved_name     → 422
    * not_found         → 404
    * timeout           → 504
    * unknown / other   → 502 Bad Gateway

RBAC matrix (PRD §5.9.1):
    * GET /bots          — any authenticated user (Viewer+)
    * POST /bots         — Editor+
    * POST /bots/{}/clone— Editor+
    * PATCH /bots/{}     — Editor+
    * DELETE /bots/{}    — Editor+
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import HermesCliAdapter, HermesCliError, LocalHostOps, ProfileFsAdapter
from app.auth.deps import get_current_user
from app.auth.rbac import Role, require_role
from app.config import get_settings
from app.db.session import get_session
from app.schemas.bot import (
    BotCloneIn,
    BotCreateIn,
    BotDeleteIn,
    BotOut,
    BotRenameIn,
)
from app.secret_filter import scrub_secrets
from app.services.bot import BotNotFoundError, BotService, DuplicateBotError

_ALLOWED_LOG_HOURS = frozenset({1, 6, 24, 72})
_LOG_TS_RE = re.compile(r"\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")

router = APIRouter(prefix="/bots", tags=["bots"])


# ----------------------------------------------------------------------
# DI factory.
# ----------------------------------------------------------------------
def get_bot_service(session: AsyncSession = Depends(get_session)) -> BotService:
    """Construct a BotService per-request.

    Wires LocalHostOps (real subprocess) → HermesCliAdapter + ProfileFsAdapter
    against the configured Hermes home. Tests override this via
    ``app.dependency_overrides[get_bot_service]`` to inject InMemoryHostOps.
    """
    settings = get_settings()
    host = LocalHostOps()
    cli = HermesCliAdapter(host)
    fs = ProfileFsAdapter(host, hermes_home=settings.hermes_home)
    return BotService(session=session, cli=cli, fs=fs, archive_dir=settings.archive_dir)


def _hermes_to_http(e: HermesCliError) -> HTTPException:
    """Translate HermesCliError.hint → HTTP status code (vocabulary contract)."""
    if e.hint == "not_found":
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    if e.hint in ("invalid_name", "reserved_name"):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    if e.hint == "duplicate":
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if e.hint == "timeout":
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(e))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


# ----------------------------------------------------------------------
# Routes.
# ----------------------------------------------------------------------
@router.get("", response_model=list[BotOut])
async def list_bots(
    q: Annotated[str | None, Query(max_length=64)] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    tag: Annotated[str | None, Query(max_length=32)] = None,  # B1
    _user: dict[str, Any] = Depends(get_current_user),
    service: BotService = Depends(get_bot_service),
) -> list[BotOut]:
    return await service.list_bots(q=q, status_filter=status_filter, tag=tag)


@router.post("", response_model=BotOut, status_code=status.HTTP_201_CREATED)
async def create_bot(
    payload: BotCreateIn,
    _user: dict[str, Any] = Depends(require_role(Role.EDITOR)),
    service: BotService = Depends(get_bot_service),
) -> BotOut:
    try:
        return await service.create_bot(payload)
    except DuplicateBotError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Bot '{e}' 已存在") from e
    except HermesCliError as e:
        raise _hermes_to_http(e) from e


@router.post("/{name}/clone", response_model=BotOut, status_code=status.HTTP_201_CREATED)
async def clone_bot(
    name: str,
    payload: BotCloneIn,
    _user: dict[str, Any] = Depends(require_role(Role.EDITOR)),
    service: BotService = Depends(get_bot_service),
) -> BotOut:
    try:
        return await service.clone_bot(name, payload)
    except DuplicateBotError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Bot '{e}' 已存在") from e
    except HermesCliError as e:
        raise _hermes_to_http(e) from e


@router.patch("/{name}", response_model=BotOut)
async def rename_bot(
    name: str,
    payload: BotRenameIn,
    _user: dict[str, Any] = Depends(require_role(Role.EDITOR)),
    service: BotService = Depends(get_bot_service),
) -> BotOut:
    try:
        return await service.rename_bot(name, payload)
    except BotNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Bot '{e}' 不存在"
        ) from e
    except DuplicateBotError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Bot '{e}' 已存在") from e
    except HermesCliError as e:
        raise _hermes_to_http(e) from e


@router.get(
    "/{name}/logs/download",
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def download_logs(
    name: str,
    request: Request,
    hours: Annotated[int, Query(ge=1, le=72)] = 1,
) -> StreamingResponse:
    """Stream the gateway log filtered to the last ``hours`` (1/6/24/72 only).

    GATEWAY-08: read-only download for forensic / hand-off scenarios. The
    endpoint reads ``cli.gateway_log_path()`` directly (Hermes v0.8 writes
    a single shared log file) and streams ``text/plain`` with an attachment
    Content-Disposition. Lines whose ISO-ish timestamp parses earlier than
    ``cutoff = now - hours`` are skipped; lines without a parseable
    timestamp are passed through verbatim (best-effort).
    """
    if hours not in _ALLOWED_LOG_HOURS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="hours 必须是 1 / 6 / 24 / 72 之一",
        )
    cli: HermesCliAdapter = request.app.state.cli
    path = cli.gateway_log_path(name)
    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    async def gen() -> AsyncIterator[bytes]:
        try:
            async with aiofiles.open(path, encoding="utf-8", errors="replace") as f:
                async for line in f:
                    ts_match = _LOG_TS_RE.match(line)
                    if ts_match:
                        try:
                            ts = datetime.fromisoformat(ts_match.group(1).replace(" ", "T"))
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=UTC)
                            if ts < cutoff:
                                continue
                        except ValueError:
                            pass
                    yield scrub_secrets(line).encode("utf-8")
        except FileNotFoundError:
            return

    return StreamingResponse(
        gen(),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}-gateway-{hours}h.log"',
        },
    )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot(
    name: str,
    payload: BotDeleteIn,
    _user: dict[str, Any] = Depends(require_role(Role.EDITOR)),
    service: BotService = Depends(get_bot_service),
) -> None:
    try:
        await service.delete_bot(name, confirm_name=payload.confirm_name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except HermesCliError as e:
        raise _hermes_to_http(e) from e
