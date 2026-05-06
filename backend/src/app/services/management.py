"""Phase 5 management services — model config, workspace, skills, health.

All filesystem reads/writes route through :class:`ProfileFsAdapter` and the
shared :class:`HostOps` port so tests can swap in :class:`InMemoryHostOps`.
The functions here are intentionally side-effect-thin: they read the YAML,
mutate the small slice the API exposes, and write back **preserving every
unknown key** so we never blow away config keys Hermes added in a future
release.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, TypedDict, cast

import yaml
from fastapi import HTTPException, status

from app.adapters.hostops import HostOps
from app.adapters.profile_fs import ProfileFsAdapter
from app.schemas.management import ModelProviderOption, SkillItem
from app.services.provider_auth import (
    CHATGPT_AUTH_API_MODE,
    CHATGPT_AUTH_BASE_URL,
    CHATGPT_AUTH_PROVIDER,
)

logger = logging.getLogger(__name__)

TERMINAL_CWD_ENV_KEY = "TERMINAL_CWD"
_CODEX_AUTH_URL_RE = re.compile(r"https://auth\.openai\.com/oauth/authorize\?\S+")
_RUNNING_CODEX_AUTH: dict[int, tuple[subprocess.Popen[bytes], int]] = {}
_HERMES_PROVIDER_CATALOG_SCRIPT = r"""
import json
import sys
import traceback

from hermes_cli.config import load_config
from hermes_cli.model_switch import list_authenticated_providers
from hermes_cli.models import CANONICAL_PROVIDERS, _PROVIDER_MODELS
from hermes_cli.providers import determine_api_mode, get_label, get_provider

current_provider = sys.argv[1]
current_model = sys.argv[2]
preferred_provider = sys.argv[3]


def coerce_dict(value):
    return value if isinstance(value, dict) else {}


def coerce_list(value):
    return value if isinstance(value, list) else []


def build_option(
    slug,
    *,
    name="",
    models=None,
    is_current=False,
    is_user_defined=False,
    is_configured=False,
    source="",
    api_url="",
    current_model_for_provider="",
):
    pdef = get_provider(slug)
    base_url = api_url or (pdef.base_url if pdef else "")
    api_mode = determine_api_mode(slug, base_url)
    auth_type = pdef.auth_type if pdef else ""
    display_name = name or (pdef.name if pdef else get_label(slug))
    provider_source = source or (pdef.source if pdef else "")

    deduped_models = []
    if current_model_for_provider:
        deduped_models.append(current_model_for_provider)
    for model_name in models or []:
        model_name = str(model_name).strip()
        if model_name and model_name not in deduped_models:
            deduped_models.append(model_name)

    return {
        "slug": slug,
        "name": display_name,
        "is_current": is_current,
        "is_user_defined": is_user_defined,
        "is_configured": is_configured,
        "models": deduped_models,
        "total_models": max(len(deduped_models), len(models or [])),
        "source": provider_source,
        "base_url": base_url or None,
        "api_mode": api_mode or None,
        "auth_type": auth_type or None,
    }


try:
    cfg = load_config()
    model_cfg = coerce_dict(cfg.get("model"))
    provider = current_provider or str(model_cfg.get("provider") or "")
    model_name = current_model or str(model_cfg.get("default") or model_cfg.get("model") or "")
    items = list_authenticated_providers(
        current_provider=provider,
        user_providers=coerce_dict(cfg.get("providers")) or None,
        custom_providers=cfg.get("custom_providers")
        if isinstance(cfg.get("custom_providers"), list)
        else None,
        max_models=50,
    )
    options = [
        build_option(
            str(item.get("slug") or ""),
            name=str(item.get("name") or ""),
            models=[str(m) for m in coerce_list(item.get("models"))],
            is_current=bool(item.get("is_current")),
            is_user_defined=bool(item.get("is_user_defined")),
            is_configured=True,
            source=str(item.get("source") or ""),
            api_url=str(item.get("api_url") or ""),
            current_model_for_provider=model_name
            if item.get("slug") == provider
            else "",
        )
        for item in items
        if item.get("slug")
    ]
    for provider_to_include in {provider, preferred_provider}:
        if provider_to_include and not any(item["slug"] == provider_to_include for item in options):
            options.append(
                build_option(
                    provider_to_include,
                    models=[str(m) for m in coerce_list(_PROVIDER_MODELS.get(provider_to_include))],
                    is_current=provider_to_include == provider,
                    is_configured=provider_to_include == provider,
                    current_model_for_provider=model_name
                    if provider_to_include == provider
                    else "",
                )
            )
    for provider_entry in CANONICAL_PROVIDERS:
        if not any(item["slug"] == provider_entry.slug for item in options):
            options.append(
                build_option(
                    provider_entry.slug,
                    name=provider_entry.label,
                    models=[str(m) for m in coerce_list(_PROVIDER_MODELS.get(provider_entry.slug))],
                    source="hermes-builtin",
                )
            )
    print(json.dumps({"providers": options}, ensure_ascii=False))
except Exception:
    traceback.print_exc(file=sys.stderr)
    raise
"""

# Heuristic keywords that flip the "dangerous" flag for a skill. Operators
# enabling such a skill must echo the bot name back as ``confirm_name``.
_DANGEROUS_KEYWORDS: frozenset[str] = frozenset(
    {
        "shell",
        "terminal",
        "bash",
        "code",
        "execute",
        "filesystem",
        "file",
        "browser",
        "network",
    }
)


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


async def read_config_yaml(fs: ProfileFsAdapter, name: str) -> dict[str, Any]:
    path = fs.config_path(name)
    if not await fs.host.path_exists(path):
        return {}
    text = await fs.host.read_text(path)
    if not text.strip():
        return {}
    parsed = yaml.safe_load(text)
    return _coerce_dict(parsed)


async def write_config_yaml(fs: ProfileFsAdapter, name: str, doc: dict[str, Any]) -> None:
    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False)
    await fs.host.write_text_atomic(fs.config_path(name), body, mode=0o600)


# ---------------------------------------------------------------------------
# Model config
# ---------------------------------------------------------------------------


def is_chatgpt_auth(provider: str | None, api_mode: str | None, base_url: str | None) -> bool:
    return (
        (provider or "").strip() == CHATGPT_AUTH_PROVIDER
        and (api_mode or "").strip() == CHATGPT_AUTH_API_MODE
        and (base_url or "").strip().rstrip("/") == CHATGPT_AUTH_BASE_URL.rstrip("/")
    )


def extract_model_block(doc: dict[str, Any]) -> dict[str, Any]:
    model = _coerce_dict(doc.get("model"))
    # Hermes accepts either ``default`` or ``model`` for the model name; we
    # prefer ``default`` (the wizard-written shape) and fall back to ``model``.
    name = model.get("default") or model.get("model")
    return {
        "provider": model.get("provider"),
        "model": name,
        "base_url": model.get("base_url"),
        "api_mode": model.get("api_mode"),
    }


def merge_model_block(
    doc: dict[str, Any],
    *,
    provider: str,
    model: str,
    base_url: str | None,
    api_mode: str | None,
) -> dict[str, Any]:
    """Return a NEW dict with ``model.*`` updated and unknown keys preserved."""
    new_doc = dict(doc)
    existing = _coerce_dict(doc.get("model"))
    merged = dict(existing)
    merged["provider"] = provider
    merged["default"] = model
    # Keep the legacy ``model.model`` key in sync if it was present previously.
    if "model" in existing:
        merged["model"] = model
    if base_url is not None:
        merged["base_url"] = base_url
    elif "base_url" in merged:
        merged.pop("base_url", None)
    if api_mode is not None:
        merged["api_mode"] = api_mode
    elif "api_mode" in merged:
        merged.pop("api_mode", None)
    new_doc["model"] = merged
    return new_doc


@contextlib.contextmanager
def _temporary_sys_path(path: Path) -> Iterator[None]:
    path_text = str(path)
    inserted = False
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
        inserted = True
    try:
        yield
    finally:
        if inserted:
            with contextlib.suppress(ValueError):
                sys.path.remove(path_text)


@contextlib.contextmanager
def _temporary_environ(values: Mapping[str, str]) -> Iterator[None]:
    previous: dict[str, str | None] = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, previous_value in previous.items():
            if previous_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous_value


def _hermes_agent_root(fs: ProfileFsAdapter) -> Path | None:
    root = fs.hermes_home / "hermes-agent"
    return root if (root / "hermes_cli").exists() else None


def _hermes_agent_python(agent_root: Path) -> Path | None:
    python_bin = agent_root / "venv" / "bin" / "python"
    return python_bin if python_bin.exists() else None


def _option_from_provider_def(
    *,
    slug: str,
    name: str | None = None,
    models: list[str] | None = None,
    is_current: bool = False,
    is_user_defined: bool = False,
    is_configured: bool = False,
    source: str = "",
    api_url: str = "",
    current_model: str | None = None,
) -> ModelProviderOption:
    base_url = api_url
    api_mode = ""
    auth_type = ""
    display_name = name or slug
    provider_source = source
    try:
        providers_mod = importlib.import_module("hermes_cli.providers")
        pdef = providers_mod.get_provider(slug)
        if pdef is not None:
            display_name = name or pdef.name or providers_mod.get_label(slug)
            base_url = base_url or pdef.base_url or ""
            auth_type = pdef.auth_type
            provider_source = provider_source or pdef.source
        else:
            display_name = name or providers_mod.get_label(slug)
        api_mode = providers_mod.determine_api_mode(slug, base_url)
    except Exception as exc:
        logger.debug("Hermes provider resolution failed for %s: %s", slug, exc)
        if slug == CHATGPT_AUTH_PROVIDER:
            display_name = name or "OpenAI Codex"
            base_url = base_url or CHATGPT_AUTH_BASE_URL
            api_mode = CHATGPT_AUTH_API_MODE
            auth_type = auth_type or "oauth_external"

    deduped_models: list[str] = []
    if current_model and current_model.strip():
        deduped_models.append(current_model.strip())
    for model_name in models or []:
        model_name = str(model_name).strip()
        if model_name and model_name not in deduped_models:
            deduped_models.append(model_name)

    return ModelProviderOption(
        slug=slug,
        name=display_name,
        is_current=is_current,
        is_user_defined=is_user_defined,
        is_configured=is_configured,
        models=deduped_models,
        total_models=max(len(deduped_models), len(models or [])),
        source=provider_source,
        base_url=base_url or None,
        api_mode=api_mode or None,
        auth_type=auth_type or None,
    )


def _fallback_provider_option(
    provider: str | None,
    *,
    model: str | None,
    is_current: bool,
    doc: dict[str, Any],
) -> ModelProviderOption | None:
    if not provider:
        return None

    base_url = ""
    name = provider
    is_user_defined = False
    source = "config"
    providers = _coerce_dict(doc.get("providers"))
    provider_cfg = _coerce_dict(providers.get(provider))
    if provider_cfg:
        name = str(provider_cfg.get("name") or provider)
        base_url = str(provider_cfg.get("api") or provider_cfg.get("url") or "")
        is_user_defined = True
        source = "user-config"
    elif provider == CHATGPT_AUTH_PROVIDER:
        name = "OpenAI Codex"
        base_url = CHATGPT_AUTH_BASE_URL
        source = "hermes"

    api_mode = CHATGPT_AUTH_API_MODE if provider == CHATGPT_AUTH_PROVIDER else None
    if provider_cfg and not api_mode:
        api_mode = "chat_completions"

    return ModelProviderOption(
        slug=provider,
        name=name,
        is_current=is_current,
        is_user_defined=is_user_defined,
        is_configured=is_current or is_user_defined,
        models=[model] if model else [],
        total_models=1 if model else 0,
        source=source,
        base_url=base_url or None,
        api_mode=api_mode,
        auth_type="oauth_external" if provider == CHATGPT_AUTH_PROVIDER else None,
    )


def _ensure_provider_option(
    options: list[ModelProviderOption],
    provider: str | None,
    *,
    current_provider: str | None,
    current_model: str | None,
    doc: dict[str, Any],
) -> list[ModelProviderOption]:
    if not provider or any(item.slug == provider for item in options):
        return options

    option = _fallback_provider_option(
        provider,
        model=current_model if provider == current_provider else None,
        is_current=provider == current_provider,
        doc=doc,
    )
    if option is not None:
        options.append(option)
    return options


def _list_model_provider_options_sync(
    fs: ProfileFsAdapter,
    name: str,
    doc: dict[str, Any],
    env: Mapping[str, str],
    preferred_provider: str | None,
) -> list[ModelProviderOption]:
    block = extract_model_block(doc)
    current_provider = str(block["provider"] or "")
    current_model = str(block["model"] or "") or None
    agent_root = _hermes_agent_root(fs)
    if agent_root is None:
        options: list[ModelProviderOption] = []
        _ensure_provider_option(
            options,
            current_provider or preferred_provider,
            current_provider=current_provider,
            current_model=current_model,
            doc=doc,
        )
        if preferred_provider and preferred_provider != current_provider:
            _ensure_provider_option(
                options,
                preferred_provider,
                current_provider=current_provider,
                current_model=current_model,
                doc=doc,
            )
        return options

    subprocess_options = _list_model_provider_options_via_hermes_python(
        agent_root=agent_root,
        profile_dir=fs.profile_dir(name),
        env=env,
        current_provider=current_provider,
        current_model=current_model or "",
        preferred_provider=preferred_provider or "",
    )
    if subprocess_options is not None:
        return subprocess_options

    profile_env = {"HERMES_HOME": str(fs.profile_dir(name)), **env}
    with _temporary_sys_path(agent_root), _temporary_environ(profile_env):
        try:
            config_mod = importlib.import_module("hermes_cli.config")
            model_switch_mod = importlib.import_module("hermes_cli.model_switch")
            models_mod = importlib.import_module("hermes_cli.models")

            cfg = config_mod.load_config()
            model_cfg = _coerce_dict(cfg.get("model"))
            provider = current_provider or str(model_cfg.get("provider") or "")
            model_name = current_model or str(model_cfg.get("default") or model_cfg.get("model") or "")
            items = model_switch_mod.list_authenticated_providers(
                current_provider=provider,
                user_providers=_coerce_dict(cfg.get("providers")) or None,
                custom_providers=cfg.get("custom_providers") if isinstance(cfg.get("custom_providers"), list) else None,
                max_models=50,
            )
            options = [
                _option_from_provider_def(
                    slug=str(item.get("slug") or ""),
                    name=str(item.get("name") or "") or None,
                    models=[str(m) for m in _coerce_list(item.get("models"))],
                    is_current=bool(item.get("is_current")),
                    is_user_defined=bool(item.get("is_user_defined")),
                    is_configured=True,
                    source=str(item.get("source") or ""),
                    api_url=str(item.get("api_url") or ""),
                    current_model=model_name if item.get("slug") == provider else None,
                )
                for item in items
                if item.get("slug")
            ]
            for provider_to_include in {provider, preferred_provider or ""}:
                if provider_to_include and not any(item.slug == provider_to_include for item in options):
                    models = [
                        str(m)
                        for m in _coerce_list(
                            models_mod._PROVIDER_MODELS.get(provider_to_include)
                        )
                    ]
                    options.append(
                        _option_from_provider_def(
                            slug=provider_to_include,
                            models=models,
                            is_current=provider_to_include == provider,
                            is_configured=provider_to_include == provider,
                            current_model=model_name if provider_to_include == provider else None,
                        )
                    )
            for provider_entry in models_mod.CANONICAL_PROVIDERS:
                if not any(item.slug == provider_entry.slug for item in options):
                    models = [
                        str(m)
                        for m in _coerce_list(
                            models_mod._PROVIDER_MODELS.get(provider_entry.slug)
                        )
                    ]
                    options.append(
                        _option_from_provider_def(
                            slug=provider_entry.slug,
                            name=provider_entry.label,
                            models=models,
                            source="hermes-builtin",
                        )
                    )
            return options
        except Exception as exc:
            logger.warning("Failed to read Hermes provider catalog for %s: %s", name, exc)
            options = []
            for provider_to_include in {current_provider, preferred_provider or ""}:
                _ensure_provider_option(
                    options,
                    provider_to_include,
                    current_provider=current_provider,
                    current_model=current_model,
                    doc=doc,
                )
            return options


def _list_model_provider_options_via_hermes_python(
    *,
    agent_root: Path,
    profile_dir: Path,
    env: Mapping[str, str],
    current_provider: str,
    current_model: str,
    preferred_provider: str,
) -> list[ModelProviderOption] | None:
    python_bin = _hermes_agent_python(agent_root)
    if python_bin is None:
        return None
    proc_env = os.environ.copy()
    proc_env.update(env)
    proc_env["HERMES_HOME"] = str(profile_dir)
    existing_pythonpath = proc_env.get("PYTHONPATH", "")
    proc_env["PYTHONPATH"] = (
        str(agent_root) if not existing_pythonpath else f"{agent_root}:{existing_pythonpath}"
    )
    try:
        proc = subprocess.run(
            [
                str(python_bin),
                "-c",
                _HERMES_PROVIDER_CATALOG_SCRIPT,
                current_provider,
                current_model,
                preferred_provider,
            ],
            cwd=agent_root,
            env=proc_env,
            text=True,
            capture_output=True,
            timeout=12,
            check=False,
        )
    except Exception as exc:
        logger.warning("Failed to launch Hermes provider catalog helper: %s", exc)
        return None
    if proc.returncode != 0:
        logger.warning("Hermes provider catalog helper failed: %s", proc.stderr.strip())
        return None
    try:
        payload = json.loads(proc.stdout)
        return [
            ModelProviderOption.model_validate(item)
            for item in _coerce_list(payload.get("providers"))
            if isinstance(item, dict)
        ]
    except Exception as exc:
        logger.warning("Hermes provider catalog helper returned invalid JSON: %s", exc)
        return None


async def list_model_provider_options(
    fs: ProfileFsAdapter,
    name: str,
    doc: dict[str, Any],
    *,
    preferred_provider: str | None = None,
) -> list[ModelProviderOption]:
    """Return Hermes-discovered provider choices for the model config UI.

    The web console treats Hermes as the source of truth: provider labels,
    curated models, base URLs, and API modes come from ``hermes_cli`` when the
    local agent install is available. The fallback only mirrors the existing
    profile config so the page remains usable in tests and partial installs.
    """
    env = await _read_model_provider_env(fs, name)
    return _list_model_provider_options_sync(fs, name, doc, env, preferred_provider)


async def _read_model_provider_env(fs: ProfileFsAdapter, name: str) -> dict[str, str]:
    """Merge global Hermes credentials with profile-local overrides."""
    env: dict[str, str] = {}
    global_env_path = fs.hermes_home / ".env"
    if await fs.host.path_exists(global_env_path):
        text = await fs.host.read_text(global_env_path)
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            env[key.strip()] = value.strip()
    env.update(await fs.read_env(name))
    return env


def selected_provider_transport(
    options: list[ModelProviderOption],
    provider: str,
    body_base_url: str | None,
    body_api_mode: str | None,
) -> tuple[str | None, str | None]:
    selected = next((option for option in options if option.slug == provider), None)
    base_url = body_base_url if body_base_url is not None else selected.base_url if selected else None
    api_mode = body_api_mode if body_api_mode is not None else selected.api_mode if selected else None
    return base_url, api_mode


class CodexAuthLaunch(TypedDict):
    authorization_url: str
    process_id: int


def _prune_codex_auth_processes() -> None:
    for pid, (proc, master_fd) in list(_RUNNING_CODEX_AUTH.items()):
        if proc.poll() is not None:
            _RUNNING_CODEX_AUTH.pop(pid, None)
            with contextlib.suppress(OSError):
                os.close(master_fd)


def _read_pty_with_timeout(master_fd: int, timeout_seconds: float) -> str | None:
    readable, _, _ = select.select([master_fd], [], [], timeout_seconds)
    if not readable:
        return None
    try:
        chunk = os.read(master_fd, 4096)
    except OSError:
        return None
    if not chunk:
        return None
    return chunk.decode(errors="replace")


def start_codex_auth_session(timeout_seconds: float = 8.0) -> CodexAuthLaunch:
    """Start ``codex login`` and return the browser authorization URL.

    The Codex CLI owns the OAuth local callback server. This function only starts
    the process, captures the URL it prints, and keeps the process alive while
    the user completes the browser authorization flow.
    """
    codex_bin = shutil.which("codex")
    if codex_bin is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="codex CLI 未安装, 无法启动 Codex auth 授权",
        )

    _prune_codex_auth_processes()
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        [codex_bin, "login"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    output: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        remaining = max(0.0, min(0.25, deadline - time.monotonic()))
        chunk = _read_pty_with_timeout(master_fd, remaining)
        if chunk is None:
            if proc.poll() is not None:
                break
            continue
        output.append(chunk)
        match = _CODEX_AUTH_URL_RE.search("".join(output))
        if match is not None:
            _RUNNING_CODEX_AUTH[proc.pid] = (proc, master_fd)
            return {"authorization_url": match.group(0), "process_id": proc.pid}

    if proc.poll() is None:
        proc.terminate()
    with contextlib.suppress(OSError):
        os.close(master_fd)
    detail = "".join(output).strip() or "codex login 未在超时时间内返回授权链接"
    raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=detail)


# ---------------------------------------------------------------------------
# Workspace path validation (WORKSPACE-03 security hardening)
# ---------------------------------------------------------------------------


def validate_workspace_path(cwd: str, hermes_home: Path) -> None:
    """Validate workspace path for security concerns.

    Raises HTTPException 422 for:
    - Paths containing ``..`` traversal components
    - Paths that resolve into the Hermes home directory
    - Paths that are symlinks (resolved != absolute)

    Non-existent valid paths pass (existence is a probe-level check, not security).
    """
    raw = Path(cwd)

    # 1. Reject .. traversal components before resolve.
    # We check the raw string parts so "a/../b" is caught even if it resolves safely.
    if ".." in raw.parts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="路径穿越字符 (..) 不被允许",
        )

    resolved = raw.resolve()
    hermes_resolved = hermes_home.resolve()

    # 2. Reject paths inside ~/.hermes/.
    # Either the path IS hermes_home or hermes_home is one of its parents.
    if resolved == hermes_resolved or hermes_resolved in resolved.parents:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="路径不能位于 Hermes 家目录 (~/.hermes/) 内",
        )

    # 3. Reject symlinks (resolved path differs from absolute path).
    if raw.is_symlink():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="路径为符号链接, 拒绝访问",
        )


# ---------------------------------------------------------------------------
# Workspace (terminal.cwd)
# ---------------------------------------------------------------------------


def extract_workspace_cwd(doc: dict[str, Any]) -> str | None:
    terminal = _coerce_dict(doc.get("terminal"))
    cwd = terminal.get("cwd")
    return cwd if isinstance(cwd, str) and cwd.strip() else None


def merge_workspace_cwd(doc: dict[str, Any], cwd: str | None) -> dict[str, Any]:
    new_doc = dict(doc)
    terminal = dict(_coerce_dict(doc.get("terminal")))
    if cwd:
        terminal["cwd"] = cwd
    else:
        terminal.pop("cwd", None)
    if terminal:
        new_doc["terminal"] = terminal
    elif "terminal" in new_doc:
        new_doc.pop("terminal", None)
    return new_doc


async def sync_workspace_env(fs: ProfileFsAdapter, name: str, cwd: str | None) -> None:
    env = await fs.read_env(name)
    if cwd:
        if env.get(TERMINAL_CWD_ENV_KEY) == cwd:
            return
        env[TERMINAL_CWD_ENV_KEY] = cwd
        await fs.write_env(name, env)
        return
    if TERMINAL_CWD_ENV_KEY in env:
        env.pop(TERMINAL_CWD_ENV_KEY, None)
        await fs.write_env(name, env)


async def reset_active_gateway_sessions(fs: ProfileFsAdapter, name: str) -> None:
    sessions_index = fs.profile_dir(name) / "sessions" / "sessions.json"
    if await fs.host.path_exists(sessions_index):
        await fs.host.write_text_atomic(sessions_index, "{}\n", mode=0o600)


def probe_workspace(cwd: str | None) -> dict[str, Any]:
    """Return ``{exists, is_directory, readable, writable, status, message}``.

    MVP uses ``pathlib`` / ``os.access`` directly on the FastAPI host. M3 will
    push this through HostOps when remote Host Agent lands.
    """
    if not cwd:
        return {
            "exists": False,
            "is_directory": False,
            "readable": False,
            "writable": False,
            "status": "unset",
            "message": "Workspace 未配置 — Hermes 默认使用 Bot Profile 目录",
        }
    p = Path(cwd)
    exists = p.exists()
    is_directory = p.is_dir()
    readable = exists and os.access(p, os.R_OK)
    writable = exists and os.access(p, os.W_OK)
    if not exists:
        return {
            "exists": False,
            "is_directory": False,
            "readable": False,
            "writable": False,
            "status": "error",
            "message": f"路径不存在: {cwd}",
        }
    if not is_directory:
        return {
            "exists": True,
            "is_directory": False,
            "readable": readable,
            "writable": False,
            "status": "error",
            "message": f"路径存在但不是目录: {cwd}",
        }
    if not readable:
        return {
            "exists": True,
            "is_directory": True,
            "readable": False,
            "writable": False,
            "status": "error",
            "message": f"目录不可读: {cwd}",
        }
    if not writable:
        return {
            "exists": True,
            "is_directory": True,
            "readable": True,
            "writable": False,
            "status": "warning",
            "message": f"目录只读, Hermes 可能无法写入产物: {cwd}",
        }
    return {
        "exists": True,
        "is_directory": True,
        "readable": True,
        "writable": True,
        "status": "ok",
        "message": "Workspace 可读可写",
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


def is_dangerous(name: str, category: str | None, description: str | None) -> bool:
    haystack = " ".join(v for v in (name, category or "", description or "") if v).lower()
    return any(keyword in haystack for keyword in _DANGEROUS_KEYWORDS)


def extract_disabled(doc: dict[str, Any]) -> list[str]:
    skills = _coerce_dict(doc.get("skills"))
    disabled = _coerce_list(skills.get("disabled"))
    return [str(item) for item in disabled if isinstance(item, str)]


def merge_disabled(doc: dict[str, Any], disabled: list[str]) -> dict[str, Any]:
    new_doc = dict(doc)
    skills = dict(_coerce_dict(doc.get("skills")))
    # Dedupe + preserve order.
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in disabled:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    if cleaned:
        skills["disabled"] = cleaned
    else:
        skills.pop("disabled", None)
    if skills:
        new_doc["skills"] = skills
    elif "skills" in new_doc:
        new_doc.pop("skills", None)
    return new_doc


def parse_skill_md(text: str) -> dict[str, Any]:
    """Parse the YAML frontmatter of a SKILL.md file.

    Tolerant: missing frontmatter or malformed YAML returns an empty dict.
    Surfaces ``name`` / ``description`` / ``category`` / ``requires_tools``.
    """
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}
    body = stripped[3:]
    end_idx = body.find("\n---")
    frontmatter = body[:end_idx] if end_idx >= 0 else body
    try:
        parsed = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    raw_tools = parsed.get("requires_tools")
    requires_tools: list[str] = []
    if isinstance(raw_tools, list):
        requires_tools = [str(t) for t in raw_tools if t]
    return {
        "name": _maybe_str(parsed.get("name")),
        "description": _maybe_str(parsed.get("description")),
        "category": _maybe_str(parsed.get("category")),
        "requires_tools": requires_tools,
    }


def check_missing_deps(skill: SkillItem) -> list[str]:
    """Return list of tools from skill.requires_tools that are not on PATH."""
    return [tool for tool in skill.requires_tools if shutil.which(tool) is None]


def _maybe_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return str(v)


async def _iter_skill_dirs(
    host: HostOps,
    root: Path,
    *,
    depth: int = 0,
    max_depth: int = 3,
) -> list[tuple[Path, Path]]:
    """Return ``[(skill_dir, skill_md_path), ...]`` under ``root``.

    Real Hermes skill installs use ``skills/<category>/<skill>/SKILL.md``;
    tests and some hand-made profile skills may use ``skills/<skill>/SKILL.md``.
    Keep the walk shallow and explicit so a stray huge tree under ``skills/``
    cannot turn this endpoint into an expensive filesystem crawl.
    """
    if not await host.path_exists(root):
        return []

    direct_skill_md = root / "SKILL.md"
    if await host.path_exists(direct_skill_md):
        return [(root, direct_skill_md)]
    if depth >= max_depth:
        return []

    found: list[tuple[Path, Path]] = []
    try:
        entries = await host.list_dir(root)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return []

    for entry in entries:
        if entry.startswith("."):
            continue
        found.extend(
            await _iter_skill_dirs(
                host,
                root / entry,
                depth=depth + 1,
                max_depth=max_depth,
            )
        )
    return found


def _fallback_skill_category(root: Path, skill_dir: Path) -> str | None:
    try:
        rel = skill_dir.relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 2:
        return parts[0]
    return None


async def sync_skills_fs(profile_skills_dir: Path, enabled_names: set[str]) -> None:
    """Filesystem diff-sync for skills enable/disable.

    - Skills in ``profile_skills_dir`` whose name is NOT in ``enabled_names``
      are moved to ``profile_skills_dir/.disabled/<name>``.
    - Skills in ``.disabled/`` whose name IS in ``enabled_names`` are moved back
      to ``profile_skills_dir/<name>``.
    - Uses Path.rename (same-filesystem atomic). Falls back to copytree+rmtree
      for cross-device moves.
    - Missing source directories are silently skipped (logs warning).
    """
    disabled_dir = profile_skills_dir / ".disabled"
    disabled_dir.mkdir(parents=True, exist_ok=True)

    # Disable skills no longer in enabled_names
    if profile_skills_dir.exists():
        for entry in list(profile_skills_dir.iterdir()):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            if entry.name not in enabled_names:
                dest = disabled_dir / entry.name
                try:
                    entry.rename(dest)
                except FileNotFoundError:
                    logger.warning("sync_skills_fs: source dir not found, skipping: %s", entry)
                except OSError:
                    # Cross-device fallback
                    try:
                        shutil.copytree(str(entry), str(dest))
                        shutil.rmtree(str(entry))
                    except Exception:
                        logger.warning(
                            "sync_skills_fs: failed to move %s → %s", entry, dest, exc_info=True
                        )

    # Re-enable skills now in enabled_names
    if disabled_dir.exists():
        for entry in list(disabled_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in enabled_names:
                dest = profile_skills_dir / entry.name
                try:
                    entry.rename(dest)
                except FileNotFoundError:
                    logger.warning("sync_skills_fs: disabled dir not found, skipping: %s", entry)
                except OSError:
                    try:
                        shutil.copytree(str(entry), str(dest))
                        shutil.rmtree(str(entry))
                    except Exception:
                        logger.warning(
                            "sync_skills_fs: failed to re-enable %s → %s", entry, dest, exc_info=True
                        )


async def discover_skills(
    fs: ProfileFsAdapter,
    host: HostOps,
    name: str,
    *,
    global_skills_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Walk ``<profile>/skills`` and the Hermes global skills dir.

    Returns a list of partially-typed dicts ready to feed into :class:`SkillItem`.
    Profile-local skills win over global skills with the same name (shadowing).
    Skills in ``<profile>/skills/.disabled/`` are included with ``enabled=False``.
    """
    items_by_name: dict[str, dict[str, Any]] = {}

    profile_skills = fs.profile_dir(name) / "skills"

    # Scan enabled profile skills
    for skill_dir, skill_md in await _iter_skill_dirs(host, profile_skills):
        try:
            text = await host.read_text(skill_md)
        except FileNotFoundError:
            continue
        meta = parse_skill_md(text)
        skill_name = meta.get("name") or skill_dir.name
        assert skill_name is not None
        items_by_name[skill_name] = {
            "name": skill_name,
            "category": meta.get("category") or _fallback_skill_category(profile_skills, skill_dir),
            "description": meta.get("description"),
            "requires_tools": meta.get("requires_tools") or [],
            "source": "profile",
            "enabled": True,
        }

    # Scan disabled profile skills (in .disabled/ subdir)
    disabled_dir = profile_skills / ".disabled"
    for skill_dir, skill_md in await _iter_skill_dirs(host, disabled_dir):
        try:
            text = await host.read_text(skill_md)
        except FileNotFoundError:
            continue
        meta = parse_skill_md(text)
        skill_name = meta.get("name") or skill_dir.name
        assert skill_name is not None
        # Disabled skills don't override enabled profile skills (same name edge-case)
        if skill_name not in items_by_name:
            items_by_name[skill_name] = {
                "name": skill_name,
                "category": meta.get("category") or _fallback_skill_category(disabled_dir, skill_dir),
                "description": meta.get("description"),
                "requires_tools": meta.get("requires_tools") or [],
                "source": "profile",
                "enabled": False,
            }

    # Scan global skills — always (not just when profile is empty)
    if global_skills_dir is not None:
        for skill_dir, skill_md in await _iter_skill_dirs(host, global_skills_dir):
            try:
                text = await host.read_text(skill_md)
            except FileNotFoundError:
                continue
            meta = parse_skill_md(text)
            skill_name = meta.get("name") or skill_dir.name
            assert skill_name is not None
            if skill_name in items_by_name:
                # Profile skill shadows this global — mark the winner
                items_by_name[skill_name]["shadowed_source"] = "global"
            else:
                items_by_name[skill_name] = {
                    "name": skill_name,
                    "category": meta.get("category")
                    or _fallback_skill_category(global_skills_dir, skill_dir),
                    "description": meta.get("description"),
                    "requires_tools": meta.get("requires_tools") or [],
                    "source": "global",
                    "enabled": True,
                }

    out: list[dict[str, Any]] = []
    for item in items_by_name.values():
        item["dangerous"] = is_dangerous(
            cast(str, item["name"]),
            cast("str | None", item.get("category")),
            cast("str | None", item.get("description")),
        )
        out.append(item)
    out.sort(key=lambda i: cast(str, i["name"]))
    return out
