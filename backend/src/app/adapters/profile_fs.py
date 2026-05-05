"""ProfileFsAdapter — filesystem operations on ~/.hermes/.

Reads/writes profile config files. Coordinates with HermesCliAdapter — the two
are intentionally separate so we can mock them independently. Phase 4 status
probe consumes :meth:`list_profiles` as the source of truth (Pitfall #1: the
``hermes gateway status`` CLI leaks across profiles in v0.8 so we can't trust it).

NFR-02 plaintext .env on disk: Hermes reads literal ``KEY=VALUE`` from .env
without decryption, so the file MUST be plaintext. Confidentiality is delivered
by mode 0o600 (owner-only) plus Fernet encryption of the SAME secret in the DB
column (BotService — Plan 02-04). That dual-store pattern is intentional:
filesystem is for Hermes; DB is for Web Console display + audit + recovery.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import anyio

from app.adapters.hostops import HostOps

logger = logging.getLogger(__name__)

# Maximum number of snapshots to keep per profile.
_SNAPSHOT_RETENTION = 10

# Keys whose values should be redacted in .env snapshots.
_SECRET_KEY_PATTERNS = re.compile(r"(SECRET|KEY|TOKEN|PASS)", re.IGNORECASE)

# BOT-08 — stricter than Hermes' own regex (Hermes allows underscore + 1..64 chars).
# We require a leading [a-z0-9] (rejects "-foo" flag-injection vectors at adapter
# layer per Pitfall #14) and 3..32 chars total. Underscore is rejected to keep
# the namespace URL-safe and human-readable.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,31}$")

# 'default' is a real Hermes profile that lives at ~/.hermes/ root, not under
# ~/.hermes/profiles/default/. Allowing users to create a Bot named 'default'
# would conflate the two and corrupt the install. We reject it at adapter layer
# (HermesCliAdapter also rejects it server-side, but this is belt-and-suspenders).
_RESERVED_NAMES = frozenset({"default"})

# Phase 4 — FEISHU_ALLOWED_USERS .env key.
# FINDING-04: comma-separated single-line value (e.g. ``ou_a,ou_b,ou_c``).
# Whitespace around entries is trimmed on read; empty entries dropped.
_ALLOWLIST_KEY = "FEISHU_ALLOWED_USERS"
_ALLOWLIST_SEPARATOR = ","


def validate_bot_name(name: str) -> str:
    """BOT-08: ``^[a-z0-9][a-z0-9-]{2,31}$`` AND not ``'default'``.

    Returns the validated name unchanged. Raises ``ValueError`` with a
    Chinese-language message suitable for surfacing as the API 400 detail.
    """
    if name in _RESERVED_NAMES:
        raise ValueError(f"Bot 名不能为保留字 '{name}'")
    if not _NAME_RE.match(name):
        raise ValueError(
            "Bot 名仅允许小写字母/数字/短横线，3-32 字符，必须以字母或数字开头"  # noqa: RUF001
        )
    return name


class ProfileFsAdapter:
    """Direct filesystem adapter for the Hermes installation root.

    Constructor injects :class:`HostOps` (the M3 Host Agent seam) — this class
    NEVER imports asyncio.subprocess, psutil, or os; all I/O routes through
    HostOps so a remote Host Agent can swap in transparently.
    """

    def __init__(self, host: HostOps, hermes_home: Path) -> None:
        self.host = host
        self.hermes_home = hermes_home
        self.profiles_root = hermes_home / "profiles"

    def profile_dir(self, name: str) -> Path:
        """Return the directory holding ``<name>``'s config.

        Pitfall #2: ``'default'`` lives at ``~/.hermes/`` (root), NOT
        ``~/.hermes/profiles/default/``. Every other named profile lives at
        ``~/.hermes/profiles/<name>/``. Adapter encapsulates this branch so
        downstream callers never have to remember it.
        """
        if name == "default":
            return self.hermes_home
        return self.profiles_root / name

    def env_path(self, name: str) -> Path:
        """``<profile_dir>/.env`` — Hermes reads this for FEISHU_APP_ID/SECRET."""
        return self.profile_dir(name) / ".env"

    def config_path(self, name: str) -> Path:
        """``<profile_dir>/config.yaml`` — Hermes profile config."""
        return self.profile_dir(name) / "config.yaml"

    async def list_profiles(self) -> list[str]:
        """Source of truth for which profiles exist (filesystem-driven).

        Includes ``'default'`` iff ``~/.hermes/config.yaml`` exists (Hermes
        creates this on first install). Includes every subdirectory of
        ``~/.hermes/profiles/`` (excluding hidden/dotfiles). Returned sorted.
        """
        names: set[str] = set()
        if await self.host.path_exists(self.hermes_home / "config.yaml"):
            names.add("default")
        if await self.host.path_exists(self.profiles_root):
            for entry in await self.host.list_dir(self.profiles_root):
                if entry and not entry.startswith("."):
                    names.add(entry)
        return sorted(names)

    async def write_env(self, name: str, env: dict[str, str]) -> None:
        """Write ``.env`` atomically with mode 0o600.

        Plaintext on disk (Hermes reads literal KEY=VALUE). Crash-safety + 600
        delivered by :meth:`HostOps.write_text_atomic` (Pitfall #8).

        Rejects values containing newlines and keys containing ``=``/newlines —
        we don't support multiline env values in MVP. Use M2 escape pattern if
        ever needed.
        """
        for key, value in env.items():
            if "\n" in value or "=" in key or "\n" in key:
                raise ValueError(f"Invalid env entry: {key!r}={value!r}")
        body = "\n".join(f"{k}={v}" for k, v in env.items()) + "\n"
        await self.host.write_text_atomic(self.env_path(name), body, mode=0o600)

    async def read_allowed_users(self, name: str) -> list[str]:
        """Return the FEISHU_ALLOWED_USERS list (comma-separated per FINDING-04).

        Empty / missing key → ``[]``. Whitespace around each entry is trimmed,
        empty entries are dropped. Order preserved from .env.
        """
        env = await self.read_env(name)
        raw = env.get(_ALLOWLIST_KEY, "")
        if not raw:
            return []
        return [v.strip() for v in raw.split(_ALLOWLIST_SEPARATOR) if v.strip()]

    async def write_allowed_users(self, name: str, users: list[str]) -> None:
        """Replace FEISHU_ALLOWED_USERS preserving every other key in .env.

        Dedupes (first occurrence wins) and trims whitespace. Rejects entries
        containing the separator (``,``) or any newline — we don't support
        escaping in MVP and a leaked separator would silently merge two
        allowlist entries.
        """
        for u in users:
            if _ALLOWLIST_SEPARATOR in u or "\n" in u or "\r" in u:
                raise ValueError(f"invalid allowlist entry (contains separator/newline): {u!r}")
        seen: list[str] = []
        seen_set: set[str] = set()
        for u in users:
            stripped = u.strip()
            if stripped and stripped not in seen_set:
                seen.append(stripped)
                seen_set.add(stripped)
        env = await self.read_env(name)
        env[_ALLOWLIST_KEY] = _ALLOWLIST_SEPARATOR.join(seen)
        await self.write_env(name, env)

    async def read_env(self, name: str) -> dict[str, str]:
        """Read ``.env`` into a ``dict``. Returns ``{}`` if missing.

        Skips blank lines, comments (``#``), and malformed lines (no ``=``).
        Trims surrounding whitespace from keys and values. Does NOT unescape —
        keep symmetric with :meth:`write_env`.
        """
        path = self.env_path(name)
        if not await self.host.path_exists(path):
            return {}
        text = await self.host.read_text(path)
        result: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            result[key.strip()] = value.strip()
        return result

    async def snapshot_profile(self, name: str) -> Path:
        """Create a timestamped snapshot of a profile's config files.

        Writes to ``<profile_dir>/.snapshots/<ISO8601>/`` containing:
        - ``config.yaml`` (verbatim copy)
        - ``.env`` (secrets redacted to ***)
        - ``SOUL.md`` (if it exists)
        - ``skills-manifest.json`` (summary of enabled/disabled skills)

        Prunes oldest snapshots to keep at most 10. Returns the snapshot path.
        Suppresses all errors internally — callers should log and continue.
        """
        profile_dir = self.profile_dir(name)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        snapshots_dir = profile_dir / ".snapshots"
        snap_path = snapshots_dir / timestamp

        def _do_snapshot() -> Path:
            snap_path.mkdir(parents=True, exist_ok=True)

            # 1. Copy config.yaml verbatim
            config_src = profile_dir / "config.yaml"
            if config_src.exists():
                shutil.copy2(config_src, snap_path / "config.yaml")

            # 2. Copy .env with secrets redacted
            env_src = profile_dir / ".env"
            if env_src.exists():
                lines = env_src.read_text().splitlines()
                redacted_lines: list[str] = []
                for line in lines:
                    if "=" in line and not line.strip().startswith("#"):
                        key, _, _ = line.partition("=")
                        if _SECRET_KEY_PATTERNS.search(key):
                            line = f"{key}=***"
                    redacted_lines.append(line)
                (snap_path / ".env").write_text("\n".join(redacted_lines) + "\n")

            # 3. Copy SOUL.md if present
            soul_src = profile_dir / "SOUL.md"
            if soul_src.exists():
                shutil.copy2(soul_src, snap_path / "SOUL.md")

            # 4. Write skills-manifest.json
            import yaml  # local import to avoid module-level dep
            disabled: list[str] = []
            config_snap = snap_path / "config.yaml"
            if config_snap.exists():
                try:
                    doc = yaml.safe_load(config_snap.read_text()) or {}
                    skills_block = doc.get("skills", {}) if isinstance(doc, dict) else {}
                    disabled = skills_block.get("disabled", []) if isinstance(skills_block, dict) else []
                except Exception:
                    disabled = []
            skills_dir = profile_dir / "skills"
            enabled: list[str] = []
            if skills_dir.exists():
                for child in sorted(skills_dir.iterdir()):
                    if not child.name.startswith("."):
                        enabled.append(child.name)
            manifest = {"disabled": disabled, "enabled": enabled}
            (snap_path / "skills-manifest.json").write_text(json.dumps(manifest, indent=2))

            # 5. Prune oldest snapshots (keep at most _SNAPSHOT_RETENTION)
            existing = sorted(snapshots_dir.iterdir())
            for old in existing[:-_SNAPSHOT_RETENTION]:
                shutil.rmtree(old, ignore_errors=True)

            return snap_path

        return await anyio.to_thread.run_sync(_do_snapshot)
