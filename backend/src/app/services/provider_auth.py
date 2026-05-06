"""Hermes provider auth-state helpers.

Hermes stores OAuth-style provider credentials in ``auth.json`` beside each
profile. The web console should not know token internals, but it does need two
small operations for ChatGPT Codex auth:

* report whether the selected provider already has local credentials
* reuse an existing local Hermes auth entry when creating/configuring a bot
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.adapters.profile_fs import ProfileFsAdapter

logger = logging.getLogger(__name__)

CHATGPT_AUTH_PROVIDER = "openai-codex"
CHATGPT_AUTH_API_MODE = "codex_responses"
CHATGPT_AUTH_BASE_URL = "https://chatgpt.com/backend-api/codex"


def auth_path(fs: ProfileFsAdapter, profile_name: str) -> Path:
    return fs.profile_dir(profile_name) / "auth.json"


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def read_auth_json(fs: ProfileFsAdapter, profile_name: str) -> dict[str, Any]:
    path = auth_path(fs, profile_name)
    if not await fs.host.path_exists(path):
        return {}
    try:
        parsed = json.loads(await fs.host.read_text(path))
    except Exception:
        logger.warning("failed to read Hermes auth.json for profile %s", profile_name, exc_info=True)
        return {}
    return _coerce_dict(parsed)


async def write_auth_json(
    fs: ProfileFsAdapter, profile_name: str, payload: dict[str, Any]
) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    await fs.host.write_text_atomic(auth_path(fs, profile_name), body, mode=0o600)


def _provider_entry(auth_state: dict[str, Any], provider: str) -> Any | None:
    providers = _coerce_dict(auth_state.get("providers"))
    if provider in providers:
        return providers[provider]
    credential_pool = _coerce_dict(auth_state.get("credential_pool"))
    if provider in credential_pool:
        return credential_pool[provider]
    return None


def has_provider_auth_state(auth_state: dict[str, Any], provider: str) -> bool:
    return bool(_provider_entry(auth_state, provider))


async def provider_authorized(
    fs: ProfileFsAdapter,
    profile_name: str,
    provider: str | None,
) -> bool:
    """Return True when the console has enough evidence that auth exists.

    For non-Codex providers the console does not yet have a generic API-key
    detector, so a configured provider is treated as authorized. The stricter
    local ``auth.json`` check is intentionally limited to ChatGPT Codex auth.
    """
    if not provider:
        return False
    if provider != CHATGPT_AUTH_PROVIDER:
        return True
    return has_provider_auth_state(await read_auth_json(fs, profile_name), provider)


async def _auth_source_profiles(
    fs: ProfileFsAdapter,
    *,
    target_profile: str,
) -> list[str]:
    candidates: list[str] = []
    if target_profile != "default":
        candidates.append("default")
    try:
        profiles = await fs.list_profiles()
    except Exception:
        logger.warning("failed to list Hermes profiles for auth reuse", exc_info=True)
        profiles = []
    for profile in profiles:
        if profile != target_profile and profile not in candidates:
            candidates.append(profile)
    return candidates


async def reuse_provider_auth(
    fs: ProfileFsAdapter,
    target_profile: str,
    provider: str = CHATGPT_AUTH_PROVIDER,
) -> bool:
    """Copy provider-local Hermes auth into ``target_profile`` when available.

    Returns True when the target is authorized after the call, False when no
    reusable local auth exists. Existing target auth for other providers is
    preserved.
    """
    if await provider_authorized(fs, target_profile, provider):
        return True

    for source_profile in await _auth_source_profiles(fs, target_profile=target_profile):
        source_auth = await read_auth_json(fs, source_profile)
        if not has_provider_auth_state(source_auth, provider):
            continue

        target_auth = await read_auth_json(fs, target_profile)
        merged = dict(target_auth)
        merged["version"] = merged.get("version") or source_auth.get("version") or 1
        merged["active_provider"] = provider
        merged["updated_at"] = datetime.now(UTC).isoformat()

        source_providers = _coerce_dict(source_auth.get("providers"))
        if provider in source_providers:
            target_providers = dict(_coerce_dict(merged.get("providers")))
            target_providers[provider] = copy.deepcopy(source_providers[provider])
            merged["providers"] = target_providers

        source_pool = _coerce_dict(source_auth.get("credential_pool"))
        if provider in source_pool:
            target_pool = dict(_coerce_dict(merged.get("credential_pool")))
            target_pool[provider] = copy.deepcopy(source_pool[provider])
            merged["credential_pool"] = target_pool

        await write_auth_json(fs, target_profile, merged)
        return True

    return False
