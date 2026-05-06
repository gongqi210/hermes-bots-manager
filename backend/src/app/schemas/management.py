"""Phase 5 management schemas — model config, workspace, skills, health, audit.

These DTOs back the simple operator-facing APIs that turn a freshly paired Bot
from "talks at all" into "talks with the model + skills the operator wants".
Scope is MVP only; richer admin features stay parked for M2/M3.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.gateway import GatewayState


class ModelProviderOption(BaseModel):
    """A Hermes-discovered provider and the curated models it exposes."""

    slug: str
    name: str
    is_current: bool = False
    is_user_defined: bool = False
    is_configured: bool = False
    models: list[str] = Field(default_factory=list)
    total_models: int = 0
    source: str = ""
    base_url: str | None = None
    api_mode: str | None = None
    auth_type: str | None = None


class ModelConfigOut(BaseModel):
    """``model.*`` slice of a Bot's ``config.yaml``.

    ``is_chatgpt_auth`` is a backend-friendly flag that's True when the
    provider+api_mode+base_url tuple matches the ChatGPT subscription auth
    setup the wizard ships as a one-click default (see Phase 4 verification).
    """

    bot_name: str
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_mode: str | None = None
    is_chatgpt_auth: bool = False
    provider_authorized: bool = False
    providers: list[ModelProviderOption] = Field(default_factory=list)


class ModelConfigUpdateIn(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    api_mode: str | None = Field(default=None, max_length=64)


class ChatgptAuthStartOut(BaseModel):
    authorization_url: str
    process_id: int
    message: str


class WorkspaceOut(BaseModel):
    bot_name: str
    cwd: str | None = None
    exists: bool = False
    is_directory: bool = False
    readable: bool = False
    writable: bool = False
    status: Literal["ok", "warning", "error", "unset"] = "unset"
    message: str = ""


class WorkspaceUpdateIn(BaseModel):
    cwd: str | None = Field(default=None, max_length=1024)


class SkillItem(BaseModel):
    name: str
    category: str | None = None
    description: str | None = None
    source: Literal["profile", "global", "uploaded"] = "profile"
    enabled: bool = True
    dangerous: bool = False
    shadowed_source: str | None = None  # e.g. "global" — this profile skill shadows a global
    missing_deps: list[str] = Field(default_factory=list)  # tool names absent from PATH
    requires_tools: list[str] = Field(default_factory=list)  # tools declared in SKILL.md


class SkillsOut(BaseModel):
    bot_name: str
    skills: list[SkillItem]
    disabled: list[str]


class SkillsUpdateIn(BaseModel):
    """Request body for ``PUT /bots/{name}/skills``.

    ``disabled`` replaces the entire ``skills.disabled`` list. ``confirm_name``
    is required ONLY when an enable transitions a "dangerous" skill from
    disabled → enabled — the router checks the diff and rejects if it's
    missing or doesn't match the path bot name.
    """

    disabled: list[str] = Field(default_factory=list)
    confirm_name: str | None = None


class HealthOut(BaseModel):
    bot_name: str
    gateway_state: GatewayState
    gateway_why: str
    model_configured: bool
    provider_authorized: bool
    workspace_status: Literal["ok", "warning", "error", "unset"]
    skills_enabled: int
    skills_total: int
    # Phase 5 plan 05-05 — overview ribbon counters.
    dangerous_skill_count: int = 0
    shadowed_skill_count: int = 0
    allowlist_preset: Literal["open", "owner_admin", "custom"] = "custom"
    overall: Literal["ok", "warning", "error"]


class AllowlistPresetsOut(BaseModel):
    """Computed allowlist presets the operator can switch between."""

    bot_name: str
    open: list[str] = Field(default_factory=list)
    owner_admin: list[str] = Field(default_factory=list)
    custom: list[str] = Field(default_factory=list)
    owner_admin_warning: str | None = None


class AllowlistPresetUpdateIn(BaseModel):
    preset: Literal["open", "owner_admin", "custom"]


class WorkspaceLibraryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    path: str
    label: str | None = None
    registered_by: int | None = None
    registered_at: datetime


class WorkspaceLibraryCreateIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    label: str | None = Field(default=None, max_length=128)


class WorkspaceReuseOption(BaseModel):
    bot_name: str
    cwd: str


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    actor_id: int | None = None
    actor_ip: str | None = None
    method: str
    path: str
    target_type: str | None = None
    target_id: str | None = None
    result: str
    error: str | None = None
    created_at: datetime
