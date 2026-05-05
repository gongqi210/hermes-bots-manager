"""Wizard endpoints — FEISHU-02, FEISHU-04, FEISHU-05.

Routes:
  GET  /bots/check-app-id            App ID uniqueness check (FEISHU-05)
  GET  /bots/{name}/wizard/run       SSE stream — 7-step wizard (FEISHU-02)
  PATCH /bots/{name}/secret          Reset App Secret (FEISHU-04)

Auth:
  - check-app-id, run_wizard: any authenticated user (Viewer+)
  - reset_secret: Editor+

Route ordering note:
  ``/check-app-id`` MUST be registered BEFORE ``/{name}/wizard/run`` — FastAPI
  matches routes in declaration order and ``check-app-id`` would otherwise be
  captured as a bot name path param.

SECRET DESIGN (FEISHU-04 + Pitfall #6):
  ``run_wizard`` reads the App Secret from DB via :func:`decrypt_str` rather
  than accepting it as a query param. This keeps the secret out of server
  access logs (uvicorn / nginx). Pre-condition: the bot row must already
  exist (from ``POST /api/v1/bots``) and ``feishu_app_secret_enc`` must be
  non-null.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import HermesCliAdapter, LocalHostOps, ProfileFsAdapter
from app.auth.crypto import decrypt_str
from app.auth.deps import get_current_user
from app.auth.rbac import Role, require_role
from app.config import get_settings
from app.db.session import get_session, get_sessionmaker
from app.models.bot import Bot
from app.schemas.bot import BotFeishuCredentialsIn, BotOut, BotSecretResetIn
from app.services.bot import AppIdConflictError, BotNotFoundError, BotService
from app.services.lark_cli import extract_open_feishu_url, stream_lark_init_lines
from app.services.wizard import WizardService

router = APIRouter(prefix="/bots", tags=["wizard"])


# ----------------------------------------------------------------------
# DI factories.
# ----------------------------------------------------------------------
def _get_wizard_service(
    session: AsyncSession = Depends(get_session),
) -> WizardService:
    """Wire LocalHostOps → HermesCliAdapter + ProfileFsAdapter for the wizard.

    Tests override this via ``app.dependency_overrides[_get_wizard_service]``
    to inject InMemoryHostOps without touching the real subprocess/fs layer.
    """
    settings = get_settings()
    host = LocalHostOps()
    cli = HermesCliAdapter(host)
    fs = ProfileFsAdapter(host, hermes_home=settings.hermes_home)
    return WizardService(cli=cli, fs=fs, session=session)


def _get_bot_service(
    session: AsyncSession = Depends(get_session),
) -> BotService:
    """Bot service for the secret-reset endpoint (DI keeps test override simple)."""
    settings = get_settings()
    host = LocalHostOps()
    cli = HermesCliAdapter(host)
    fs = ProfileFsAdapter(host, hermes_home=settings.hermes_home)
    return BotService(session=session, cli=cli, fs=fs, archive_dir=settings.archive_dir)


# ----------------------------------------------------------------------
# Routes — order matters! /check-app-id MUST come before /{name}/wizard/run.
# ----------------------------------------------------------------------
@router.get("/check-app-id")
async def check_app_id_available(
    app_id: str = Query(..., max_length=64),
    _user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """FEISHU-05: returns ``{"available": bool, "conflict_bot": str | None}``.

    ``conflict_bot`` is the NAME of the conflicting Bot (never its secret).
    """
    result = await session.scalars(select(Bot).where(Bot.feishu_app_id == app_id))
    existing = result.first()
    return {
        "available": existing is None,
        "conflict_bot": existing.name if existing else None,
    }


@router.get("/{name}/wizard/run")
async def run_wizard(
    name: str = Path(..., max_length=32),
    feishu_app_id: str = Query(default="", max_length=64),
    domain: str = Query(default="feishu"),
    connection_mode: str = Query(default="websocket"),
    group_strategy: str = Query(default="mention"),
    _user: dict[str, Any] = Depends(get_current_user),
    service: WizardService = Depends(_get_wizard_service),
) -> StreamingResponse:
    """FEISHU-02 + FEISHU-03: SSE stream for the 7-step wizard.

    Pre-condition: the bot row must already exist (created via POST /bots)
    and ``feishu_app_secret_enc`` must be non-null. The App Secret is decrypted
    from DB at request time and passed to the WizardService — it never lives
    in the request URL or query string.

    Auth is validated BEFORE :class:`StreamingResponse` (Pitfall #8 — once the
    response starts streaming, status code is already committed as 200).
    """
    # Eager DB read with a fresh short-lived session so the streaming generator
    # doesn't hold the request session open through 6+ subprocess calls.
    maker = get_sessionmaker()
    async with maker() as lookup_session:
        result = await lookup_session.scalars(select(Bot).where(Bot.name == name))
        bot = result.first()
        if bot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bot '{name}' 不存在",
            )
        if not bot.feishu_app_secret_enc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Bot '{name}' 尚未设置 App Secret，请先通过创建表单填写",  # noqa: RUF001
            )
        secret_plain = decrypt_str(bot.feishu_app_secret_enc)
        resolved_app_id = feishu_app_id or bot.feishu_app_id or ""

    secret = SecretStr(secret_plain)
    return StreamingResponse(
        service.run(
            name,
            resolved_app_id,
            secret,
            domain,
            connection_mode,
            group_strategy,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )


@router.patch("/{name}/feishu-credentials", response_model=BotOut)
async def update_feishu_credentials(
    name: str,
    payload: BotFeishuCredentialsIn,
    _user: dict[str, Any] = Depends(require_role(Role.EDITOR)),
    service: BotService = Depends(_get_bot_service),
) -> BotOut:
    """Wizard step 2: save Feishu App ID, Secret, domain, mode, and group strategy."""
    try:
        return await service.update_feishu_credentials(name, payload)
    except BotNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot '{name}' 不存在",
        ) from None
    except AppIdConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"App ID 已被 Bot '{e}' 占用",
        ) from None


# ----------------------------------------------------------------------
# lark-cli config init streaming SSE — 向导第一步.
# ----------------------------------------------------------------------
def _get_lark_init_stream() -> Any:
    """DI hook returning an async generator factory for tests to override."""
    return stream_lark_init_lines


@router.get("/{name}/lark-app/init")
async def lark_app_init(
    request: Request,
    name: str = Path(..., max_length=32),
    _user: dict[str, Any] = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
    stream_factory: Any = Depends(_get_lark_init_stream),
) -> StreamingResponse:
    """Run ``lark-cli config init --new --lang zh`` and stream QR/link output.

    Events:
      - ``{"type": "line", "text": "..."}`` raw stdout line, including newline
      - ``{"type": "url", "url": "https://..."}`` parsed open.feishu.cn URL
      - ``{"type": "missing"}`` lark-cli is not installed
      - ``{"type": "done"}`` child process finished
    """
    import json

    result = await session.scalars(select(Bot).where(Bot.name == name))
    if result.first() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot '{name}' 不存在",
        )

    async def gen() -> AsyncIterator[bytes]:
        seen_url: str | None = None
        stream = stream_factory(stop_check=request.is_disconnected)
        try:
            async for line in stream:
                if line.startswith("__lark_cli_missing__"):
                    yield (
                        b"data: "
                        + json.dumps({"type": "missing"}, ensure_ascii=False).encode("utf-8")
                        + b"\n\n"
                    )
                    break
                if line.startswith("__lark_cli_timeout__"):
                    yield (
                        b"data: "
                        + json.dumps({"type": "timeout"}, ensure_ascii=False).encode("utf-8")
                        + b"\n\n"
                    )
                    break
                payload: dict[str, Any] = {"type": "line", "text": line}
                yield (
                    b"data: "
                    + json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    + b"\n\n"
                )
                if seen_url is None:
                    found = extract_open_feishu_url(line)
                    if found:
                        seen_url = found
                        yield (
                            b"data: "
                            + json.dumps({"type": "url", "url": found}, ensure_ascii=False).encode(
                                "utf-8"
                            )
                            + b"\n\n"
                        )
                        # We only need the QR/link. Stop the blocking CLI instead
                        # of waiting for browser completion; the user fills
                        # App ID/Secret back into the console manually.
                        break
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()
        yield b"data: " + json.dumps({"type": "done"}).encode("utf-8") + b"\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/{name}/secret", response_model=BotOut)
async def reset_secret(
    name: str,
    payload: BotSecretResetIn,
    _user: dict[str, Any] = Depends(require_role(Role.EDITOR)),
    service: BotService = Depends(_get_bot_service),
) -> BotOut:
    """FEISHU-04: replace ``feishu_app_secret_enc`` in DB and rewrite ``.env``."""
    try:
        return await service.reset_secret(name, payload.feishu_app_secret)
    except BotNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot '{name}' 不存在",
        ) from None


# Re-export AsyncIterator placeholder so mypy strict doesn't yell about unused.
_ = AsyncIterator
