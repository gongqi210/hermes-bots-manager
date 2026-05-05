"""Gateway control router — Phase 4 GATEWAY-01 / GATEWAY-02 / GATEWAY-07/08.

Endpoints
---------

* ``GET    /api/v1/bots/{bot_name}/gateway/status`` — Viewer+; the 5-state
  status with a Chinese why-string (D-17 / D-18) and triple-PID validation
  per NFR-06.
* ``POST   /api/v1/bots/{bot_name}/gateway/start``    — Editor+; serialized
  via ``SupervisorRegistry.lock_for(bot_name)`` (per-profile lock,
  GATEWAY-03 / D-14, 120s timeout). Returns the 200 OK once the new state
  has been observed (or the polling window elapses).
* ``POST   /api/v1/bots/{bot_name}/gateway/stop``     — Editor+; calls
  ``hermes -p <bot> gateway stop`` (no ``--all``, FINDING-05).
* ``POST   /api/v1/bots/{bot_name}/gateway/restart``  — Editor+; calls
  ``hermes -p <bot> gateway restart``; same polling + onboarding-hook
  semantics as start (a successful restart counts as "Gateway 运行中" for
  the 3-min KPI).

The router composes its dependencies from ``request.app.state`` (set up
in the lifespan, MAJOR 4):

* ``request.app.state.supervisor_registry`` — per-Bot Lock + add_bot
* ``request.app.state.cli`` — :class:`HermesCliAdapter`
* ``request.app.state.host`` — :class:`HostOps`
* ``request.app.state.fs`` — :class:`ProfileFsAdapter`
* ``request.app.state.write_pairing`` — closure-bound writer; the registry
  already holds a reference (set on ``start_all``); we read it here for
  parity with the wizard finish path so a future per-request override is
  a one-line change.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.hermes_cli import HermesCliAdapter, HermesCliError
from app.adapters.hostops import HostOps
from app.adapters.profile_fs import ProfileFsAdapter
from app.auth.deps import get_current_user
from app.auth.rbac import Role, require_role
from app.config import get_settings
from app.db.session import get_session
from app.schemas.gateway import GatewayActionResponse, GatewayStatusOut
from app.services.bot import BotService
from app.services.gateway.supervisor import SupervisorRegistry
from app.services.onboarding_tracker import OnboardingTracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bots/{bot_name}/gateway", tags=["gateway"])

# D-14: per-profile lock timeout. 120s gives us slack vs the slowest known
# CLI op (gateway start ~20s on first run + Hermes init).
LOCK_TIMEOUT_SEC = 120

# NFR-06: poll the pid file for at most 30s after start/restart returns,
# at 200ms cadence (150 iterations x 0.2s = 30s), looking for state="running".
_POLL_ITERATIONS = 150
_POLL_INTERVAL_SEC = 0.2

# Cap how many lines of the gateway.log we ship back in the action response
# envelope. GATEWAY-02 浮层 spec is "last 200 lines".
_RECENT_LOG_TAIL_LINES = 200


def _new_bot_service(session: AsyncSession) -> BotService:
    """Construct a BotService for status computation.

    The Phase 2 BotService constructor wants ``cli`` / ``fs`` / ``archive_dir``
    so we wire them off settings + the lifespan-managed adapters via the
    request — the gateway router builds its own when it needs to call mutation
    methods. For status read-paths we only need ``self.session`` so we pass
    placeholders for the rest (the adapters are NEVER touched on the read path).
    """
    settings = get_settings()
    # These are placeholders — :meth:`compute_gateway_status` reads its
    # adapters from kwargs, never from ``self``.
    from app.adapters import HermesCliAdapter as _Cli
    from app.adapters import LocalHostOps
    from app.adapters import ProfileFsAdapter as _Fs

    host = LocalHostOps()
    return BotService(
        session=session,
        cli=_Cli(host),
        fs=_Fs(host, hermes_home=settings.hermes_home),
        archive_dir=settings.archive_dir,
    )


async def _tail_log(path: Path, n: int) -> list[str]:
    """Return the last ``n`` lines of ``path``; ``[]`` if missing."""
    try:
        async with aiofiles.open(path, "rb") as f:
            await f.seek(0, 2)
            size = await f.tell()
            read_from = max(0, size - 64 * 1024)
            await f.seek(read_from)
            data = await f.read()
    except FileNotFoundError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[-n:]


async def _wait_for_running(
    session: AsyncSession,
    bot_name: str,
    *,
    host: HostOps,
    fs: ProfileFsAdapter,
    cli: HermesCliAdapter,
) -> GatewayStatusOut:
    """Poll up to NFR-06 budget for state=running; return whatever we observe."""
    last_status: GatewayStatusOut | None = None
    for _ in range(_POLL_ITERATIONS):
        last_status = await _new_bot_service(session).compute_gateway_status(
            bot_name, host=host, fs=fs, cli=cli
        )
        if last_status.state == "running":
            return last_status
        await asyncio.sleep(_POLL_INTERVAL_SEC)
    # Best observation we have — caller decides if it's an error envelope.
    if last_status is None:
        last_status = await _new_bot_service(session).compute_gateway_status(
            bot_name, host=host, fs=fs, cli=cli
        )
    return last_status


def _hermes_to_http(action: str, e: HermesCliError) -> HTTPException:
    """Translate a Phase-4 gateway HermesCliError to an HTTPException."""
    if e.hint == "timeout":
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"{action} 超时: Hermes 未在 30s 内完成",
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"{action}失败: {e.hint} — {str(e)[:200]}",
    )


@router.get(
    "/status",
    response_model=GatewayStatusOut,
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def gateway_status(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> GatewayStatusOut:
    cli: HermesCliAdapter = request.app.state.cli
    host: HostOps = request.app.state.host
    fs: ProfileFsAdapter = request.app.state.fs
    return await _new_bot_service(session).compute_gateway_status(
        bot_name, host=host, fs=fs, cli=cli
    )


async def _gateway_action(
    *,
    request: Request,
    session: AsyncSession,
    bot_name: str,
    action: Literal["start", "stop", "restart"],
    user: dict[str, Any],
) -> GatewayActionResponse:
    registry: SupervisorRegistry = request.app.state.supervisor_registry
    cli: HermesCliAdapter = request.app.state.cli
    host: HostOps = request.app.state.host
    fs: ProfileFsAdapter = request.app.state.fs
    # MAJOR 4: pairing writer is owned by the lifespan and re-used by the
    # registry. We touch ``request.app.state.write_pairing`` here so the
    # wizard finish path and the gateway start path both surface the same
    # symbol — keeps the wiring contract grep-discoverable.
    _write_pairing = request.app.state.write_pairing

    lock = registry.lock_for(bot_name)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=LOCK_TIMEOUT_SEC)
    except TimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway 操作繁忙, 请稍后再试",
        ) from e
    try:
        try:
            if action == "start":
                await cli.gateway_start(bot_name)
            elif action == "stop":
                await cli.gateway_stop(bot_name)
            elif action == "restart":
                await cli.gateway_restart(bot_name)
        except HermesCliError as e:
            raise _hermes_to_http(action, e) from e

        if action in ("start", "restart"):
            status_out = await _wait_for_running(session, bot_name, host=host, fs=fs, cli=cli)
            # Ensure the per-Bot supervisor is alive so its hub fans out the
            # post-start log lines + UI-less pairing intercept stays armed.
            try:
                await registry.add_bot(bot_name)
            except Exception:
                logger.warning(
                    "registry.add_bot failed for %s (lifespan not booted?)",
                    bot_name,
                    exc_info=True,
                )
            try:
                await OnboardingTracker.hook_gateway_running(session, user["id"])
            except Exception:
                logger.warning(
                    "onboarding hook_gateway_running failed for user %s",
                    user["id"],
                    exc_info=True,
                )
            await session.commit()
        else:
            # stop: read the latest status snapshot (don't poll for running).
            status_out = await _new_bot_service(session).compute_gateway_status(
                bot_name, host=host, fs=fs, cli=cli
            )

        return GatewayActionResponse(
            bot_name=bot_name,
            action=action,
            new_state=status_out.state,
            recent_log_tail=await _tail_log(
                cli.gateway_log_path(bot_name),
                _RECENT_LOG_TAIL_LINES,
            ),
        )
    finally:
        lock.release()


@router.post(
    "/start",
    response_model=GatewayActionResponse,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def gateway_start(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> GatewayActionResponse:
    return await _gateway_action(
        request=request,
        session=session,
        bot_name=bot_name,
        action="start",
        user=current_user,
    )


@router.post(
    "/stop",
    response_model=GatewayActionResponse,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def gateway_stop(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> GatewayActionResponse:
    return await _gateway_action(
        request=request,
        session=session,
        bot_name=bot_name,
        action="stop",
        user=current_user,
    )


@router.post(
    "/restart",
    response_model=GatewayActionResponse,
    dependencies=[Depends(require_role(Role.EDITOR))],
)
async def gateway_restart(
    bot_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> GatewayActionResponse:
    return await _gateway_action(
        request=request,
        session=session,
        bot_name=bot_name,
        action="restart",
        user=current_user,
    )
