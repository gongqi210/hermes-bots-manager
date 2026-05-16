"""Helpers for writing Hermes-compatible Feishu runtime env keys.

Hermes' Feishu runtime reads ``FEISHU_GROUP_POLICY`` (allowlist|open|disabled)
and ``FEISHU_REQUIRE_MENTION`` (true|false). The console previously wrote
``FEISHU_GROUP_STRATEGY``, which Hermes ignored — group @ messages got
admitted to the dedup cache then rejected. This helper normalizes the
mapping in one place so wizard.py and bot.py cannot drift.
"""

from __future__ import annotations


def apply_feishu_runtime_env(
    env: dict[str, str],
    *,
    domain: str,
    group_strategy: str,
) -> dict[str, str]:
    """Mutate ``env`` to contain the Hermes-compatible Feishu runtime keys.

    - group_strategy="mention" → GROUP_POLICY=open,     REQUIRE_MENTION=true
    - group_strategy="all"     → GROUP_POLICY=open,     REQUIRE_MENTION=false
    - group_strategy="block"   → GROUP_POLICY=disabled, REQUIRE_MENTION=true

    Always sets FEISHU_CONNECTION_MODE=websocket and removes the stale
    FEISHU_GROUP_STRATEGY key. FEISHU_DOMAIN=lark only for ``lark``.
    """
    env["FEISHU_CONNECTION_MODE"] = "websocket"
    if domain == "lark":
        env["FEISHU_DOMAIN"] = "lark"
    else:
        env.pop("FEISHU_DOMAIN", None)

    if group_strategy == "all":
        env["FEISHU_GROUP_POLICY"] = "open"
        env["FEISHU_REQUIRE_MENTION"] = "false"
    elif group_strategy == "block":
        env["FEISHU_GROUP_POLICY"] = "disabled"
        env["FEISHU_REQUIRE_MENTION"] = "true"
    else:  # "mention" (default)
        env["FEISHU_GROUP_POLICY"] = "open"
        env["FEISHU_REQUIRE_MENTION"] = "true"

    env.pop("FEISHU_GROUP_STRATEGY", None)
    return env
