"""Audit log query router — Phase 5.

The audit middleware writes one row per mutating request. This router exposes
a simple paginated read API so the AuditPage can render a table. RBAC: Viewer+.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Role, require_role
from app.db.session import get_session
from app.models.audit import AuditLog
from app.schemas.management import AuditEntry

router = APIRouter(tags=["audit"])


@router.get(
    "/audit",
    response_model=list[AuditEntry],
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def list_audit(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    result: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    if result:
        stmt = stmt.where(AuditLog.result == result)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)
