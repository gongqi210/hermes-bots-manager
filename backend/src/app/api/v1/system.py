from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    # OBS-01: FastAPI / SQLite / Hermes CLI status.
    sqlite_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        sqlite_status = "error"
    return {
        "fastapi": "ok",
        "sqlite": sqlite_status,
        "hermes_cli": "unknown (phase2)",  # filled by Phase 2
    }
