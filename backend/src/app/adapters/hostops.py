"""HostOps Port — single seam between FastAPI service layer and the host machine.

M1: ``LocalHostOps`` (subprocess + filesystem on this box).
M3: ``RemoteHostOps`` (SSH or REST to a Host Agent) — TODO when Phase >=8 lands.
Tests: an ``InMemoryHostOps`` fake will be added in Plan 02-02 alongside
``HermesCliAdapter`` tests.

Every Hermes CLI invocation **must** go through :meth:`HostOps.run_hermes`
(NFR-05). The Protocol is ``runtime_checkable`` so service-layer code can use
``isinstance(host, HostOps)`` for sanity assertions if it wants.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CliResult:
    """Outcome of a single ``hermes`` invocation.

    ``returncode == -9`` is reserved for the timeout path (see
    :meth:`LocalHostOps.run_hermes`); callers MUST treat it as a hard
    failure even though no signal-9 was raised.
    """

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ProcessInfo:
    """Snapshot of a host process at the moment it was inspected.

    ``environ`` may be empty when the OS denies env-read (psutil.AccessDenied);
    the rest of the fields stay populated. Callers that need the env for
    the cmdline-match check must defensively handle the empty case.
    """

    pid: int
    cmdline: list[str]
    environ: dict[str, str]
    is_alive: bool


@runtime_checkable
class HostOps(Protocol):
    """Port for every host-side operation. See module docstring for context."""

    async def run_hermes(
        self,
        args: list[str],
        *,
        timeout_sec: float = 30.0,
        extra_env: dict[str, str] | None = None,
    ) -> CliResult: ...

    async def run_command(
        self,
        args: list[str],
        *,
        timeout_sec: float = 30.0,
        extra_env: dict[str, str] | None = None,
    ) -> CliResult:
        """Run an arbitrary command (NOT the hermes binary). INTERNAL USE ONLY.

        Phase 3 addition for pip-show / pip-install of ``lark-oapi``. Unlike
        :meth:`run_hermes` this does NOT prepend the Hermes binary and does NOT
        apply the ``-flag`` whitelist (pip args like ``-m`` and ``show`` are
        not on the hermes whitelist).

        SECURITY: Callers MUST NOT pass user-controlled strings in ``args``.
        Only used for hardcoded ``pip show`` / ``pip install`` with pinned
        package names (eg ``lark-oapi==1.5.5``).
        """
        ...

    async def read_text(self, path: Path) -> str: ...

    async def write_text_atomic(self, path: Path, content: str, *, mode: int = 0o600) -> None: ...

    async def path_exists(self, path: Path) -> bool: ...

    async def list_dir(self, path: Path) -> list[str]: ...

    async def remove_path(self, path: Path) -> None: ...

    def get_process_info(self, pid: int) -> ProcessInfo | None: ...

    async def signal_zero(self, pid: int) -> bool:
        """Return True iff ``os.kill(pid, 0)`` succeeds (process exists & signal-deliverable).

        NFR-06 third-layer liveness check — ``psutil.pid_exists`` is layer 2
        (process-table presence); this is layer 3 (verifies the process accepts
        a no-op signal). On macOS/Linux a ``PermissionError`` from kill(0) means
        the process exists but is owned by another UID — still alive, return True.
        """
        ...

    async def read_active_profile(self) -> str | None:
        """Return the currently active Hermes profile name, or ``None`` if none.

        Used by status_decider to discriminate the singleton-gateway "wrong
        profile" case (Pitfall #1). Implementation prefers the
        ``~/.hermes/active_profile`` file (symlink or plain content) and falls
        back to running ``hermes profile list`` and reading the row marked ``◆``.

        FINDING-05: no documented file shape; we tolerate either a symlink
        target or a single-line file content. CLI fallback ensures Phase 4
        works even when the file does not exist.
        """
        ...
