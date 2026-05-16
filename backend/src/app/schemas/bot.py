"""Bot Pydantic schemas — In/Out/Patch.

Name validation lives here AND in
:func:`app.adapters.profile_fs.validate_bot_name` AND in HermesCliAdapter — the
three layers are intentional defense in depth (Pitfall #14): Pydantic catches
the API boundary, ProfileFsAdapter catches direct service-layer calls, and
Hermes itself catches anything that leaks past both.

The same rejection rules apply at every layer:
* Reserved literal: ``default``
* Regex: ``^[a-z0-9][a-z0-9-]{2,31}$`` (3-32 chars, lowercase alphanum + dash,
  must start with [a-z0-9] — defeats ``-flag-injection`` style attacks).

``BotOut`` carries only ``feishu_app_secret_last4`` derived from the encrypted
column — full plaintext NEVER returns from the API (NFR-02 / BOT-08).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

from app.adapters.status_decider import BotStatus

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,31}$")
_RESERVED_NAMES = {"default"}

# Pydantic-side length bound (regex bound is inside _validate_name).
BotName = Annotated[str, StringConstraints(min_length=3, max_length=32)]


def _validate_name(v: str) -> str:
    if v in _RESERVED_NAMES:
        raise ValueError(f"Bot 名不能为保留字 '{v}'")
    if not _NAME_RE.match(v):
        raise ValueError(
            "Bot 名仅允许小写字母/数字/短横线，3-32 字符，必须以字母或数字开头"  # noqa: RUF001
        )
    return v


class BotCreateIn(BaseModel):
    name: BotName
    feishu_app_id: str | None = Field(default=None, max_length=64)
    feishu_app_secret: SecretStr | None = None
    tags: list[str] = Field(default_factory=list)
    # Phase 3 wizard fields (FEISHU-01). Optional with backward-compat defaults
    # — Phase 2 callers that omitted these still validate successfully.
    domain: Literal["feishu", "lark"] = "feishu"
    connection_mode: Literal["websocket"] = "websocket"
    group_strategy: Literal["mention", "block", "all"] = "mention"

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class BotCloneIn(BaseModel):
    new_name: BotName

    @field_validator("new_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class BotRenameIn(BaseModel):
    new_name: BotName

    @field_validator("new_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        return _validate_name(v)


class BotDeleteIn(BaseModel):
    """Body for DELETE /api/v1/bots/{name}.

    ``confirm_name`` MUST equal the path param — the endpoint compares them and
    raises 400 on mismatch. Belt-and-suspenders against ``DELETE`` typos.
    """

    confirm_name: str


class BotPatchIn(BaseModel):
    """Body for PATCH /api/v1/bots/{name}.

    Phase 2 supports rename + tags only. Phase 3 will add ``feishu_app_id`` and
    ``feishu_app_secret`` for the binding flow; until then those fields stay
    out of the schema.
    """

    new_name: BotName | None = None
    tags: list[str] | None = None

    @field_validator("new_name")
    @classmethod
    def check_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_name(v)


class BotFeishuCredentialsIn(BaseModel):
    """Body for ``PATCH /api/v1/bots/{name}/feishu-credentials``.

    Wizard step 2 saves the App ID and App Secret after the user finishes the
    Feishu browser setup, with the same domain/mode/group defaults as Phase 3.
    """

    feishu_app_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    feishu_app_secret: SecretStr
    domain: Literal["feishu", "lark"] = "feishu"
    connection_mode: Literal["websocket"] = "websocket"
    group_strategy: Literal["mention", "block", "all"] = "mention"


class BotFeishuPolicyIn(BaseModel):
    """Body for ``PATCH /api/v1/bots/{name}/feishu-policy``.

    Live-edit the Feishu group response policy after a Bot is created.
    The App Secret is NOT in this body — it stays untouched in DB + ``.env``.
    """

    group_strategy: Literal["mention", "block", "all"]


class BotSecretResetIn(BaseModel):
    """Body for ``PATCH /api/v1/bots/{name}/secret`` (FEISHU-04).

    SecretStr ensures the value never appears in tracebacks/repr; required
    field — caller MUST supply the new secret (no clear-by-omission).
    """

    feishu_app_secret: SecretStr


class BotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    feishu_app_id: str | None = None
    # Last 4 chars of *plaintext* Secret. Derived from feishu_app_secret_enc by
    # BotService. None when no Secret is set yet.
    feishu_app_secret_last4: str | None = None
    model_name: str | None = None
    tags: list[str] = []
    skills_count: int = 0
    today_message_count: int = 0  # placeholder — Phase 4
    last_heartbeat_at: datetime | None = None  # placeholder — Phase 4
    status: BotStatus
    why: str
    last_active_at: datetime | None = None
    created_at: datetime
    # Phase 3 wizard config snapshot (FEISHU-01). Populated from DB row.
    domain: str = "feishu"
    connection_mode: str = "websocket"
    group_strategy: str = "mention"
