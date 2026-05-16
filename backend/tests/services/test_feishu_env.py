"""Unit tests for the Feishu runtime env helper."""

from __future__ import annotations

from app.services.feishu_env import apply_feishu_runtime_env


def test_mention_maps_to_open_plus_require_mention() -> None:
    env: dict[str, str] = {}
    apply_feishu_runtime_env(env, domain="feishu", group_strategy="mention")
    assert env["FEISHU_GROUP_POLICY"] == "open"
    assert env["FEISHU_REQUIRE_MENTION"] == "true"
    assert env["FEISHU_CONNECTION_MODE"] == "websocket"
    assert "FEISHU_DOMAIN" not in env
    assert "FEISHU_GROUP_STRATEGY" not in env


def test_all_maps_to_open_no_mention() -> None:
    env: dict[str, str] = {}
    apply_feishu_runtime_env(env, domain="lark", group_strategy="all")
    assert env["FEISHU_GROUP_POLICY"] == "open"
    assert env["FEISHU_REQUIRE_MENTION"] == "false"
    assert env["FEISHU_DOMAIN"] == "lark"


def test_block_maps_to_disabled() -> None:
    env: dict[str, str] = {}
    apply_feishu_runtime_env(env, domain="feishu", group_strategy="block")
    assert env["FEISHU_GROUP_POLICY"] == "disabled"
    assert env["FEISHU_REQUIRE_MENTION"] == "true"


def test_strips_stale_group_strategy_and_lark_domain() -> None:
    env = {
        "FEISHU_GROUP_STRATEGY": "all",
        "FEISHU_DOMAIN": "lark",
    }
    apply_feishu_runtime_env(env, domain="feishu", group_strategy="mention")
    assert "FEISHU_GROUP_STRATEGY" not in env
    assert "FEISHU_DOMAIN" not in env
