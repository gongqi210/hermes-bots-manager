"""Schema-level tests for Phase 3 Bot wizard fields.

Validates that:
- BotCreateIn accepts new wizard fields with backward-compatible defaults
- BotCreateIn rejects invalid values for the new Literal-typed fields
- BotSecretResetIn enforces SecretStr + required field
- Bot ORM model carries the new domain/connection_mode/group_strategy columns
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.schemas.bot import BotCreateIn, BotSecretResetIn


def test_bot_create_in_accepts_wizard_fields() -> None:
    """Phase 3: domain / connection_mode / group_strategy validate cleanly."""
    payload = BotCreateIn(
        name="alpha",
        feishu_app_id="cli_x",
        feishu_app_secret="secret",  # type: ignore[arg-type]
        domain="feishu",
        connection_mode="websocket",
        group_strategy="mention",
    )
    assert payload.domain == "feishu"
    assert payload.connection_mode == "websocket"
    assert payload.group_strategy == "mention"


def test_bot_create_in_rejects_invalid_connection_mode() -> None:
    with pytest.raises(ValidationError):
        BotCreateIn(name="alpha", connection_mode="webhook")  # type: ignore[arg-type]


def test_bot_create_in_rejects_invalid_group_strategy() -> None:
    with pytest.raises(ValidationError):
        BotCreateIn(name="alpha", group_strategy="custom")  # type: ignore[arg-type]


def test_bot_create_in_rejects_invalid_domain() -> None:
    with pytest.raises(ValidationError):
        BotCreateIn(name="alpha", domain="slack")  # type: ignore[arg-type]


def test_bot_create_in_backward_compat_no_wizard_fields() -> None:
    """Phase 2 callers pass only name + maybe app_id/secret; defaults apply."""
    payload = BotCreateIn(name="alpha")
    # Defaults match the established Phase 3 values.
    assert payload.domain == "feishu"
    assert payload.connection_mode == "websocket"
    assert payload.group_strategy == "mention"


def test_bot_create_in_lark_domain_accepted() -> None:
    payload = BotCreateIn(name="alpha", domain="lark")
    assert payload.domain == "lark"


def test_bot_secret_reset_in_requires_feishu_app_secret() -> None:
    with pytest.raises(ValidationError):
        BotSecretResetIn()  # type: ignore[call-arg]


def test_bot_secret_reset_in_uses_secret_str() -> None:
    payload = BotSecretResetIn(feishu_app_secret="new-secret")  # type: ignore[arg-type]
    assert isinstance(payload.feishu_app_secret, SecretStr)
    # str(secret) should NOT show the plaintext.
    assert "new-secret" not in str(payload.feishu_app_secret)
    assert payload.feishu_app_secret.get_secret_value() == "new-secret"


def test_bot_model_has_phase3_columns() -> None:
    """ORM model declares domain/connection_mode/group_strategy attrs."""
    from app.models.bot import Bot

    bot = Bot(name="alpha")
    # Defaults set on the model; SQLAlchemy applies them at INSERT time so
    # in-memory creation may leave them None until flush. Assert attrs exist.
    assert hasattr(bot, "domain")
    assert hasattr(bot, "connection_mode")
    assert hasattr(bot, "group_strategy")
