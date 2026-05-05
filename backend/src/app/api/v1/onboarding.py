"""Onboarding KPI 埋点 router — D-19 / 3-min funnel.

The 5 funnel hooks are written by other handlers (login, wizard finish,
gateway start, pairing approve) via :class:`OnboardingTracker`. This router
exposes:

* ``GET /onboarding/runs`` — paginated history for the dashboard.
* ``POST /onboarding/{run_id}/mark-message-received`` — D-19 fallback when
  the user manually confirms the first @-bot reply landed.

RBAC: Viewer+ for both endpoints. Users only see their own runs; cross-user
access is rejected with 404 (deliberately avoids leaking the existence of
other operators' runs).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.rbac import Role, require_role
from app.db.session import get_session
from app.models.onboarding_run import OnboardingRun
from app.schemas.onboarding import OnboardingRunOut
from app.services.onboarding_tracker import OnboardingTracker

router = APIRouter(tags=["onboarding"])


@router.get(
    "/onboarding/runs",
    response_model=list[OnboardingRunOut],
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def list_onboarding_runs(
    limit: int = Query(default=10, ge=1, le=100),
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OnboardingRun]:
    """Most-recent ``limit`` runs for the current user, newest-first."""
    rows = (
        (
            await session.execute(
                select(OnboardingRun)
                .where(OnboardingRun.user_id == current_user["id"])
                .order_by(OnboardingRun.started_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post(
    "/onboarding/{run_id}/mark-message-received",
    dependencies=[Depends(require_role(Role.VIEWER))],
)
async def mark_message_received(
    run_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """D-19 fallback — user manually confirms the first @-bot reply landed."""
    row = (
        await session.execute(
            select(OnboardingRun).where(
                OnboardingRun.id == run_id,
                OnboardingRun.user_id == current_user["id"],
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    await OnboardingTracker.hook_first_message(session, current_user["id"], run_id=run_id)
    await session.flush()
    return {"id": row.id, "status": row.status}
