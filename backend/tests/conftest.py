from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.models import (  # noqa: F401
    AuditLog,
    Bot,
    OnboardingRun,
    Pairing,
    User,
    WorkspaceLibrary,
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip smoke tests unless ``pytest -m smoke`` is explicit."""
    marker_expr: str = config.getoption("-m") or ""  # type: ignore[assignment]
    if "smoke" in marker_expr:
        # Running smoke explicitly — don't skip.
        return
    skip_smoke = pytest.mark.skip(reason="opt-in smoke test (run with `pytest -m smoke`)")
    for item in items:
        if "smoke" in item.keywords:
            item.add_marker(skip_smoke)


def _hermes_on_path() -> bool:
    return shutil.which("hermes") is not None


def _set_pragmas(dbapi_conn: Any, _: Any) -> None:
    cur = dbapi_conn.cursor()
    for pragma in (
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=5000",
        "PRAGMA synchronous=NORMAL",
        "PRAGMA foreign_keys=ON",
    ):
        cur.execute(pragma)
    cur.close()


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    os.environ["HERMES_CONSOLE_DATABASE_URL"] = db_url
    os.environ["HERMES_CONSOLE_JWT_SECRET"] = "test-secret-at-least-32-chars-long-pad"
    os.environ["HERMES_CONSOLE_MASTER_KEY_PATH"] = str(tmp_path / "master.key")

    # Reset module-level singletons in app.config/session/crypto/deps
    import app.config as c
    import app.db.session as s

    c._settings = None
    s._engine = None
    s._sessionmaker = None

    # Reset optional module singletons if those modules are imported.
    try:
        import app.auth.crypto as cr

        cr._fernet = None
    except ImportError:
        pass
    try:
        import app.auth.deps as d

        d._revoked.clear()
    except ImportError:
        pass

    eng = create_async_engine(db_url, future=True)
    event.listen(eng.sync_engine, "connect", _set_pragmas)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Point the app's session module at the same test engine so middleware/endpoints
    # and tests share state.
    s._engine = eng
    s._sessionmaker = async_sessionmaker(bind=eng, expire_on_commit=False, class_=AsyncSession)

    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        yield s
