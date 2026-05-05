"""Test fakes for adapter unit tests. NOT importable from src/.

``InMemoryHostOps`` records every ``run_hermes`` call (args + timeout) and
serves scripted ``CliResult``s — letting downstream plans unit-test
``HermesCliAdapter`` / ``ProfileFsAdapter`` / ``BotService`` without ever
touching a real subprocess or filesystem.

Usage::

    host = InMemoryHostOps()
    host.queue_response(["profile", "list"], CliResult(0, fixture, ""))
    adapter = HermesCliAdapter(host)
    result = await adapter.profile_list()
    assert host.calls[0][0] == ["profile", "list"]
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.hostops import CliResult, ProcessInfo


class InMemoryHostOps:
    """In-memory ``HostOps`` fake. Records calls; returns scripted ``CliResult``.

    Matching rules for ``queue_response``:

    * ``args=None`` matches any invocation (greedy fallback).
    * Otherwise must equal the actual ``args`` list element-for-element.

    First match wins; queued entries are *not* consumed (a single fixture can
    answer multiple identical calls). Falls back to ``set_default_response``
    (which itself defaults to ``CliResult(0, "", "")``).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], float]] = []
        self.fs: dict[Path, str] = {}
        self.process_table: dict[int, ProcessInfo] = {}
        self._responses: list[tuple[list[str] | None, CliResult]] = []
        self._default_response: CliResult = CliResult(returncode=0, stdout="", stderr="")
        # Phase 3: separate scripting for run_command (pip ops) so wizard tests
        # don't collide with hermes-binary scripted responses.
        self.command_calls: list[tuple[list[str], float]] = []
        self._command_responses: list[tuple[list[str] | None, CliResult]] = []
        self._default_command_response: CliResult = CliResult(returncode=0, stdout="", stderr="")
        # Phase 4: layer-3 liveness probe + active profile tracker.
        # ``alive_pids`` is the set of pids that ``signal_zero`` returns True for.
        # ``active_profile`` is what ``read_active_profile`` returns (None = none).
        self.alive_pids: set[int] = set()
        self.active_profile: str | None = None
        self.signal_zero_calls: list[int] = []

    # ---- subprocess scripting -------------------------------------------------

    def queue_response(self, args: list[str] | None, result: CliResult) -> None:
        """Queue a response. ``args=None`` is a wildcard fallback."""
        self._responses.append((args, result))

    def set_default_response(self, result: CliResult) -> None:
        """Set the response returned when no queued matcher fires."""
        self._default_response = result

    def queue_command_response(self, args: list[str] | None, result: CliResult) -> None:
        """Phase 3: queue a response for :meth:`run_command` (pip ops).

        ``args=None`` is a wildcard fallback. Same matching semantics as
        :meth:`queue_response` but routed to the separate command queue so
        hermes-binary tests don't accidentally consume pip responses.
        """
        self._command_responses.append((args, result))

    def set_default_command_response(self, result: CliResult) -> None:
        """Set the response returned when no queued :meth:`run_command` matcher fires."""
        self._default_command_response = result

    # ---- HostOps Protocol implementation -------------------------------------

    async def run_hermes(
        self,
        args: list[str],
        *,
        timeout_sec: float = 30.0,
        extra_env: dict[str, str] | None = None,
    ) -> CliResult:
        self.calls.append((list(args), timeout_sec))
        for matcher, result in self._responses:
            if matcher is None or matcher == args:
                return result
        return self._default_response

    async def run_command(
        self,
        args: list[str],
        *,
        timeout_sec: float = 30.0,
        extra_env: dict[str, str] | None = None,
    ) -> CliResult:
        """Phase 3: simulate :meth:`HostOps.run_command` for pip operations.

        Mirrors :meth:`run_hermes` matching semantics but reads from
        ``_command_responses`` and appends to ``command_calls`` so tests can
        assert pip args independently from hermes args.
        """
        self.command_calls.append((list(args), timeout_sec))
        for matcher, result in self._command_responses:
            if matcher is None or matcher == args:
                return result
        return self._default_command_response

    async def read_text(self, path: Path) -> str:
        if path not in self.fs:
            raise FileNotFoundError(path)
        return self.fs[path]

    async def write_text_atomic(self, path: Path, content: str, *, mode: int = 0o600) -> None:
        self.fs[path] = content

    async def path_exists(self, path: Path) -> bool:
        if path in self.fs:
            return True
        prefix = str(path).rstrip("/") + "/"
        return any(str(p).startswith(prefix) for p in self.fs)

    async def list_dir(self, path: Path) -> list[str]:
        prefix = str(path).rstrip("/") + "/"
        names = {
            str(p)[len(prefix) :].split("/", 1)[0] for p in self.fs if str(p).startswith(prefix)
        }
        return sorted(names)

    async def remove_path(self, path: Path) -> None:
        self.fs.pop(path, None)

    def get_process_info(self, pid: int) -> ProcessInfo | None:
        return self.process_table.get(pid)

    async def signal_zero(self, pid: int) -> bool:
        """Phase 4: layer-3 liveness probe — returns True iff ``pid`` is in
        ``alive_pids`` (or auto-derives from ``process_table[pid].is_alive``)."""
        self.signal_zero_calls.append(pid)
        if pid in self.alive_pids:
            return True
        info = self.process_table.get(pid)
        if info is not None:
            return info.is_alive
        return False

    async def read_active_profile(self) -> str | None:
        """Phase 4: return the test-configured active profile (set via attr)."""
        return self.active_profile
