"""LocalHostOps — the only :class:`HostOps` impl in M1.

Wraps ``asyncio.create_subprocess_exec`` (NFR-05) plus a small filesystem
toolkit (atomic write, list, exists, remove) and a synchronous
``psutil``-based process inspector.

Anything that touches the host **must** go through this adapter. ``ruff``
keeps unrelated modules from importing ``subprocess`` directly via the
project lint rules; this file is the only place ``asyncio.create_subprocess_exec``
should appear.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from pathlib import Path

import anyio
import psutil

from app.adapters.hostops import CliResult, ProcessInfo

# Whitelist of CLI flags allowed in args lists. Defense in depth against an
# attacker-controlled bot name reaching the subprocess as an unintended flag
# (Pitfall #14 in 02-RESEARCH.md). Includes both `--long` and `-short` forms.
_ALLOWED_FLAGS: frozenset[str] = frozenset(
    {
        "--no-alias",
        "--clone",
        "--clone-all",
        "--clone-from",
        "-y",
        "--yes",
        "-o",
        "--output",
        "-p",
        "-n",
        "-f",
        "--level",
        "--session",
        "--since",
        "--version",
        "--help",
    }
)


def _atomic_write_blocking(tmp: Path, final: Path, content: str, mode: int) -> None:
    """Synchronous half of write_text_atomic; runs inside anyio threadpool.

    Pattern: open with O_CREAT|O_WRONLY|O_TRUNC + intended mode → write →
    fsync → close → os.replace → chmod (umask may have stripped bits, so we
    re-apply explicitly).
    """
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, final)
    os.chmod(final, mode)


def _signal_zero_sync(pid: int) -> bool:
    """Synchronous wrapper around ``os.kill(pid, 0)``.

    Layer-3 liveness probe (NFR-06):
      * Returns True when kill(0) succeeds — process exists & accepts signals.
      * Returns False on ``ProcessLookupError`` — pid is not in the process table.
      * Returns True on ``PermissionError`` — process exists but owned by
        another UID (we still get a "no" signal-deliverable answer, treat alive).
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _remove_path_blocking(path: Path) -> None:
    """Idempotent rm — file, dir, symlink, or already-gone all OK."""
    if path.is_symlink() or path.is_file():
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
        return
    # Path doesn't exist — nothing to do (idempotent).


class LocalHostOps:
    """The only :class:`HostOps` impl in M1. See module docstring."""

    # Class attribute so tests can ``monkeypatch.setattr`` the binary path.
    # Falls back to plain ``hermes`` (PATH lookup) for production.
    HERMES_BINARY: str = os.environ.get("HERMES_BINARY", "hermes")

    async def run_hermes(
        self,
        args: list[str],
        *,
        timeout_sec: float = 30.0,
        extra_env: dict[str, str] | None = None,
    ) -> CliResult:
        """Invoke ``hermes`` with the given args. Single seam for NFR-05.

        On timeout returns ``CliResult(returncode=-9, stdout="", stderr="timeout")``
        — callers (typically ``HermesCliAdapter``) translate that into a
        ``HermesCliError`` with a friendly message.
        """
        # Belt-and-suspenders: refuse any arg that *looks* like a flag but
        # isn't on the whitelist. Catches both `--foo` and `-x` accidental
        # injection if a bot name ever leaks through validation.
        for a in args:
            if a.startswith("-") and a not in _ALLOWED_FLAGS:
                raise ValueError(f"refused arg: {a!r}")

        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)

        proc = await asyncio.create_subprocess_exec(
            self.HERMES_BINARY,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except TimeoutError:
            # Pitfall #6: SIGKILL (Hermes ignores SIGTERM in some paths) +
            # await wait() to reap the zombie before returning.
            proc.kill()
            await proc.wait()
            return CliResult(returncode=-9, stdout="", stderr="timeout")

        return CliResult(
            returncode=proc.returncode if proc.returncode is not None else 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def run_command(
        self,
        args: list[str],
        *,
        timeout_sec: float = 30.0,
        extra_env: dict[str, str] | None = None,
    ) -> CliResult:
        """Run an arbitrary command (NOT the hermes binary). Phase 3 addition.

        Used by :class:`HermesCliAdapter.check_lark_oapi` /
        :meth:`install_lark_oapi` to invoke ``python3 -m pip ...`` without
        going through the hermes binary or the ``-flag`` whitelist.

        SECURITY: Callers MUST NOT pass user-controlled strings in ``args``.
        Only invoked with hardcoded pip args + pinned package names. This
        method intentionally has NO whitelist — pip's argv (``-m``, ``show``,
        ``install``) doesn't fit the hermes flag set, and applying it here
        would block the legitimate use case.
        """
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except TimeoutError:
            # Same SIGKILL + reap pattern as run_hermes (Pitfall #6).
            proc.kill()
            await proc.wait()
            return CliResult(returncode=-9, stdout="", stderr="timeout")

        return CliResult(
            returncode=proc.returncode if proc.returncode is not None else 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def read_text(self, path: Path) -> str:
        return await anyio.to_thread.run_sync(_read_text_blocking, path)

    async def write_text_atomic(self, path: Path, content: str, *, mode: int = 0o600) -> None:
        # Ensure parent dir exists with safe perms (mirrors Phase 1 crypto.py).
        await anyio.to_thread.run_sync(_ensure_parent_blocking, path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        await anyio.to_thread.run_sync(_atomic_write_blocking, tmp, path, content, mode)

    async def path_exists(self, path: Path) -> bool:
        return bool(await anyio.to_thread.run_sync(path.exists))

    async def list_dir(self, path: Path) -> list[str]:
        return await anyio.to_thread.run_sync(_list_dir_blocking, path)

    async def remove_path(self, path: Path) -> None:
        await anyio.to_thread.run_sync(_remove_path_blocking, path)

    async def signal_zero(self, pid: int) -> bool:
        """NFR-06 layer-3 liveness probe — see ``_signal_zero_sync`` for semantics."""
        return await anyio.to_thread.run_sync(_signal_zero_sync, pid)

    async def read_active_profile(self) -> str | None:
        """Return the active Hermes profile name. File-first, CLI fallback.

        Tries ``~/.hermes/active_profile`` (either a symlink or a plain
        single-line file). Falls back to parsing ``hermes profile list`` for
        the row marked with the ``◆`` glyph. Returns ``None`` if neither
        source yields an answer.
        """
        active_path = Path.home() / ".hermes" / "active_profile"

        def _read_active_file_blocking() -> str | None:
            try:
                if active_path.is_symlink():
                    target = active_path.readlink()
                    name = target.name.strip()
                    return name or None
                if active_path.exists():
                    content = active_path.read_text(encoding="utf-8").strip()
                    return content or None
            except (FileNotFoundError, OSError):
                return None
            return None

        from_file = await anyio.to_thread.run_sync(_read_active_file_blocking)
        if from_file:
            return from_file

        # Fallback: parse `hermes profile list` for ◆ marker row.
        try:
            result = await self.run_hermes(["profile", "list"], timeout_sec=10)
        except Exception:
            return None
        if result.returncode != 0:
            return None
        # Local import to avoid circular dependency at module load.
        from app.adapters.parsers import parse_profile_list

        for row in parse_profile_list(result.stdout):
            if row.is_active:
                return row.name
        return None

    def get_process_info(self, pid: int) -> ProcessInfo | None:
        """Synchronous psutil read. None when the PID doesn't exist.

        Catches ``AccessDenied`` for ``environ()`` only (some processes block
        env read while still exposing cmdline). Returns ``ProcessInfo`` with
        an empty ``environ`` dict in that case so callers can still consult
        cmdline.
        """
        if not psutil.pid_exists(pid):
            return None
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return None
        try:
            cmdline = proc.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            cmdline = []
        try:
            environ = dict(proc.environ())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            environ = {}
        try:
            is_alive = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            is_alive = False
        return ProcessInfo(pid=pid, cmdline=cmdline, environ=environ, is_alive=is_alive)


def _read_text_blocking(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _list_dir_blocking(path: Path) -> list[str]:
    return [p.name for p in sorted(Path(path).iterdir())]


def _ensure_parent_blocking(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
