"""Pydantic schemas (DTOs) package."""

from __future__ import annotations

from app.schemas.gateway import (
    AllowlistOut,
    AllowlistUpdateIn,
    GatewayActionRequest,
    GatewayActionResponse,
    GatewayState,
    GatewayStatusOut,
    WSTokenRequest,
    WSTokenResponse,
)
from app.schemas.management import (
    AuditEntry,
    ChatgptAuthStartOut,
    HealthOut,
    ModelConfigOut,
    ModelConfigUpdateIn,
    SkillItem,
    SkillsOut,
    SkillsUpdateIn,
    WorkspaceOut,
    WorkspaceUpdateIn,
)
from app.schemas.onboarding import MarkFirstMessageIn, OnboardingRunOut
from app.schemas.pairing import PairingActionResponse, PairingListItem, PairingOut

__all__ = [
    "AllowlistOut",
    "AllowlistUpdateIn",
    "AuditEntry",
    "ChatgptAuthStartOut",
    "GatewayActionRequest",
    "GatewayActionResponse",
    "GatewayState",
    "GatewayStatusOut",
    "HealthOut",
    "MarkFirstMessageIn",
    "ModelConfigOut",
    "ModelConfigUpdateIn",
    "OnboardingRunOut",
    "PairingActionResponse",
    "PairingListItem",
    "PairingOut",
    "SkillItem",
    "SkillsOut",
    "SkillsUpdateIn",
    "WSTokenRequest",
    "WSTokenResponse",
    "WorkspaceOut",
    "WorkspaceUpdateIn",
]
