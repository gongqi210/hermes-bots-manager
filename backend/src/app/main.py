from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.adapters.hermes_cli import HermesCliAdapter
from app.adapters.local_hostops import LocalHostOps
from app.adapters.profile_fs import ProfileFsAdapter
from app.api.v1 import allowlist as allowlist_router
from app.api.v1 import audit as audit_router
from app.api.v1 import auth as auth_router
from app.api.v1 import bots as bots_router
from app.api.v1 import gateway as gateway_router
from app.api.v1 import management as management_router
from app.api.v1 import onboarding as onboarding_router
from app.api.v1 import pairings as pairings_router
from app.api.v1 import system as system_router
from app.api.v1 import wizard as wizard_router
from app.api.v1 import ws_tokens as ws_tokens_router
from app.api.v1.management import workspace_library_router
from app.auth.crypto import get_fernet
from app.config import get_settings
from app.db.session import get_sessionmaker
from app.logging_config import configure_logging
from app.middleware.audit import AuditMiddleware
from app.services.archive import cleanup_old_archives
from app.services.gateway.pairing_writer import expire_old_pairings, make_pairing_writer
from app.services.gateway.supervisor import SupervisorRegistry
from app.ws import gateway_logs as ws_gateway_logs

logger = logging.getLogger(__name__)

# Pairing TTL cleanup cadence (GATEWAY-12). 60s gives the operator near-real-time
# expiry visibility without thrashing SQLite.
_PAIRING_TTL_LOOP_INTERVAL_SEC = 60.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()

    # NFR-04 banner — print loudly on startup.
    banner = settings.single_worker_banner
    print("\033[33m" + banner + "\033[0m", flush=True)  # yellow
    logger.warning(banner)

    # NFR-02 / Pitfall #6: bootstrap master key (chmod 600 check happens inside).
    get_fernet()

    # 30-day archive cleanup. One-shot scan on startup (NFR-04 single-worker
    # constraint makes a real scheduler unnecessary; daily-cron pattern is M2).
    archive_task = asyncio.create_task(cleanup_old_archives(settings.archive_dir))

    # Phase 4: SupervisorRegistry + pairing TTL cleanup task. The registry owns
    # the shared LogTailer + dispatcher + per-Bot Supervisors so pairing capture
    # works even with zero WS clients connected (GATEWAY-04 / GATEWAY-10).
    host = LocalHostOps()
    fs = ProfileFsAdapter(host=host, hermes_home=settings.hermes_home)
    cli = HermesCliAdapter(host=host)
    sessionmaker = get_sessionmaker()
    write_pairing = make_pairing_writer(sessionmaker)

    registry = SupervisorRegistry()
    await registry.start_all(
        fs=fs, host=host, log_path=cli.gateway_log_path(), write_pairing=write_pairing
    )

    # MAJOR 4: expose composed dependencies on app.state so Wave 3 REST handlers
    # (04-05) can reach them without rebuilding the wiring graph.
    app.state.supervisor_registry = registry
    app.state.write_pairing = write_pairing
    app.state.host = host
    app.state.fs = fs
    app.state.cli = cli

    async def _pairing_ttl_loop() -> None:
        while True:
            try:
                await asyncio.sleep(_PAIRING_TTL_LOOP_INTERVAL_SEC)
                await expire_old_pairings(sessionmaker)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("pairing TTL loop iteration failed")

    ttl_task = asyncio.create_task(_pairing_ttl_loop(), name="PairingTTLCleanup")

    try:
        yield
    finally:
        # Cancel the TTL loop first — it owns no shared resources, so it
        # exits cleanly on cancel without blocking the registry shutdown.
        ttl_task.cancel()
        await asyncio.gather(ttl_task, return_exceptions=True)
        await registry.shutdown_all()
        archive_task.cancel()
        await asyncio.gather(archive_task, return_exceptions=True)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        # FastAPI /docs kept on for dev; production should wrap with auth (Phase 6).
    )
    app.add_middleware(AuditMiddleware)
    app.include_router(auth_router.router, prefix="/api/v1")
    app.include_router(system_router.router, prefix="/api/v1")
    # Phase 4: WS-token issuer + onboarding KPI router. No prefix collisions
    # with bots_router so order is not load-bearing here.
    app.include_router(ws_tokens_router.router, prefix="/api/v1")
    app.include_router(onboarding_router.router, prefix="/api/v1")
    app.include_router(pairings_router.router, prefix="/api/v1")
    # Allowlist router lives under /bots/{name}/allowlist; register before
    # the catch-all bots_router (declaration-order matters in FastAPI).
    app.include_router(allowlist_router.router, prefix="/api/v1")
    # Phase 5 management endpoints (model-config / workspace / skills / health)
    # also live under /bots/{name}/...; register before bots_router for the
    # same declaration-order reason.
    app.include_router(management_router.router, prefix="/api/v1")
    app.include_router(workspace_library_router, prefix="/api/v1")
    # Audit list endpoint is /api/v1/audit — flat, no collision with bots.
    app.include_router(audit_router.router, prefix="/api/v1")
    # IMPORTANT: wizard router includes /bots/check-app-id which must register
    # BEFORE the generic /bots/{name} routes in bots_router (FastAPI matches
    # routes in declaration order — see wizard.py module docstring).
    app.include_router(wizard_router.router, prefix="/api/v1")
    # Gateway router lives under /bots/{name}/gateway so it must register
    # BEFORE the catch-all bots_router (declaration order matters in FastAPI).
    app.include_router(gateway_router.router, prefix="/api/v1")
    app.include_router(bots_router.router, prefix="/api/v1")
    # WebSocket router carries its own full path under /api/v1/ws/... — no
    # prefix here. WS routing is independent of REST so order doesn't matter.
    app.include_router(ws_gateway_logs.router)
    return app


app = create_app()
