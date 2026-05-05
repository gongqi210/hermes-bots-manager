"""Phase 5 management services — model config, workspace, skills, health.

All filesystem reads/writes route through :class:`ProfileFsAdapter` and the
shared :class:`HostOps` port so tests can swap in :class:`InMemoryHostOps`.
The functions here are intentionally side-effect-thin: they read the YAML,
mutate the small slice the API exposes, and write back **preserving every
unknown key** so we never blow away config keys Hermes added in a future
release.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import HTTPException, status

from app.adapters.hostops import HostOps
from app.adapters.profile_fs import ProfileFsAdapter
from app.schemas.management import SkillItem

logger = logging.getLogger(__name__)

CHATGPT_AUTH_PROVIDER = "openai-codex"
CHATGPT_AUTH_API_MODE = "codex_responses"
CHATGPT_AUTH_BASE_URL = "https://chatgpt.com/backend-api/codex"
CHATGPT_AUTH_DEFAULT_MODEL = "gpt-5.5"
TERMINAL_CWD_ENV_KEY = "TERMINAL_CWD"

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
