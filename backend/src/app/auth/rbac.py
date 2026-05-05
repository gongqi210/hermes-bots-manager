from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from fastapi import Depends, HTTPException, status


class Role(StrEnum):
    OWNER = "Owner"
    ADMIN = "Admin"
    EDITOR = "Editor"
    VIEWER = "Viewer"


_RANK: dict[str, int] = {"Viewer": 0, "Editor": 1, "Admin": 2, "Owner": 3}


def role_rank(role: Role | str) -> int:
    value = role.value if isinstance(role, Role) else role
    return _RANK[value]


def require_role(minimum: Role) -> Callable[..., Awaitable[dict[str, Any]]]:
    """FastAPI Depends factory. Returns a dependency that raises 403 when user.role < minimum.

    Usage:
        @router.post("/bots", dependencies=[Depends(require_role(Role.ADMIN))])
        async def create_bot(...): ...
    """
    # Deferred import to avoid cycles (deps.py imports rbac.py).
    from app.auth.deps import get_current_user

    async def _checker(
        current_user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        if role_rank(current_user["role"]) < role_rank(minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要 {minimum.value} 及以上权限",
            )
        return current_user

    return _checker
