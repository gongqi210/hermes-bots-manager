"""GatewaySupervisor + SupervisorRegistry — Phase 4 GATEWAY-04 / GATEWAY-10.

Architecture
------------

Hermes v0.8 is a singleton-gateway (one launchd / systemd service writing to
``~/.hermes/logs/gateway.log``). All profiles share the file. Per FINDING-03
there is no per-line profile marker in production logs, so the supervision
graph looks like:

    +-----------------------+      one-shared-LogTailer
    | LogTailer (singleton) | --------------------------+
    +-----------------------+                           |
                                                        v
                             +-------------------------------+
                             | SupervisorRegistry dispatcher |
                             | (one consumer task)           |
                             +---+---+---+-------------------+
                                 |   |   |
                  ---fan-out via per-bot inbox queues--
                                 |   |   |
                       v         v         v
                    +---------+ +---------+ +---------+
                    | Sup foo | | Sup bar | | Sup baz |
                    +---------+ +---------+ +---------+
                       |
                       +--> _line_belongs_to_me() — FINDING-03 active-profile check
                       +--> hub.publish(line) — fan-out to WS subscribers
                       +--> extract_pairing → write_pairing — UI-less DB write

Why a dispatcher instead of every Supervisor calling ``shared_tailer.iter_lines()``?
``iter_lines()`` reads from the tailer's queue with ``asyncio.Queue.get()``,
which is single-consumer — N supervisors competing on the same queue would
each see only ~1/N of the lines, and the active-profile owner could miss its
own pairings to a sibling supervisor's "this isn't mine, skip" branch. The
registry's dispatcher decouples the tailer (single producer/single consumer)
from the per-Bot pipeline (fan-out write to N bounded inboxes).

FINDING-03 active-profile check
-------------------------------

Hypothesis 2 from HERMES_V08_FINDINGS.md FINDING-03 was selected: the only
reliable way to attribute a gateway-log line to a Bot in Hermes v0.8 is to
ask :meth:`HostOps.read_active_profile` and accept iff the answer matches
``self.bot_name``. Hypothesis 1 (per-line ``[profile=foo]`` marker) was
rejected because no such marker exists in v0.8 (FINDING-03).

Lifecycle
---------

* Lifespan startup creates the registry, starts the shared LogTailer + the
  dispatcher task, and starts one Supervisor per ``.env``-configured Bot
  (D-01 / GATEWAY-04).
* Lifespan shutdown cancels every Supervisor + the dispatcher + the tailer
  but does NOT call ``hermes gateway stop`` (D-03: Hermes processes outlive
  the console; the next start re-attaches and resumes pairing intercepts).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.adapters.hostops import HostOps
from app.adapters.log_tail import LogTailer
from app.adapters.profile_fs import ProfileFsAdapter
from app.services.gateway.broadcast_hub import BroadcastHub
from app.services.gateway.pairing_extractor import PairingCandidate, extract_pairing

logger = logging.getLogger(__name__)

# Per-Supervisor inbox cap. The dispatcher fans out to N supervisors; if one
# falls behind we drop on its inbox rather than on the shared tailer queue
# so the others keep flowing.
_INBOX_MAXSIZE = 2000

# Shutdown timeout — the FastAPI lifespan must not hang waiting for a
# task that won't observe the cancel; 5s is the agreed bound.
_SHUTDOWN_TIMEOUT_SEC = 5.0

WritePairingFn = Callable[[PairingCandidate], Awaitable[None]]


class GatewaySupervisor:
    """Per-Bot long-lived task — owns one :class:`BroadcastHub` and reads its
    own inbox queue (filled by :class:`SupervisorRegistry`'s dispatcher).

    The Supervisor publishes every active-profile-matched line to its hub
    AND runs ``extract_pairing`` against it — pairing capture is independent
    of WS subscription state (GATEWAY-04 / GATEWAY-10).
    """

    def __init__(
        self,
        bot_name: str,
        hub: BroadcastHub,
        write_pairing: WritePairingFn,
        host: HostOps,
    ) -> None:
        self.bot_name = bot_name
        self.hub = hub
        self.write_pairing = write_pairing
        self.host = host
        self.inbox: asyncio.Queue[str] = asyncio.Queue(maxsize=_INBOX_MAXSIZE)
        self.dropped_inbox_count = 0
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def deliver(self, line: str) -> None:
        """Non-blocking inbox enqueue. Drops on overflow with a counter bump."""
        try:
            self.inbox.put_nowait(line)
        except asyncio.QueueFull:
            self.dropped_inbox_count += 1
            logger.warning(
                "supervisor inbox full for bot=%s (dropped=%d)",
                self.bot_name,
                self.dropped_inbox_count,
            )

    async def _line_belongs_to_me(self, line: str) -> bool:
        """FINDING-03 Hypothesis 2: accept iff our bot_name == active profile.

        Hermes v0.8 gateway.log carries no per-line profile marker (FINDING-03),
        so we ask :meth:`HostOps.read_active_profile` on every line. The
        active-profile lookup is cheap (single read of ``~/.hermes/active_profile``
        in production; constant-time attr read in the InMemoryHostOps fake).
        """
        active = await self.host.read_active_profile()
        return active == self.bot_name

    async def process_line(self, line: str) -> None:
        """Filter → publish → extract pairing. Used directly by tests; the
        :meth:`run` loop calls this for every inbox line.
        """
        if not await self._line_belongs_to_me(line):
            return
        # Sync, non-blocking — works with zero subscribers (GATEWAY-04).
        self.hub.publish(line)
        candidate = extract_pairing(line, bot_name=self.bot_name)
        if candidate is not None:
            try:
                await self.write_pairing(candidate)
            except Exception:
                logger.exception("pairing write failed for bot=%s", self.bot_name)

    async def run(self) -> None:
        """Long-lived loop: pull from inbox → process_line."""
        try:
            while not self._stop.is_set():
                line = await self.inbox.get()
                await self.process_line(line)
        except asyncio.CancelledError:
            raise

    def start(self) -> None:
        """Spawn the run task. Idempotent — re-start is a noop if already running."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self.run(), name=f"GatewaySupervisor[{self.bot_name}]")

    async def shutdown(self) -> None:
        """Cancel the task and wait up to ``_SHUTDOWN_TIMEOUT_SEC``.

        Does NOT terminate any Hermes Gateway process (D-03) — those are
        managed by launchd/systemd and outlive the console.
        """
        self._stop.set()
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=_SHUTDOWN_TIMEOUT_SEC)


class SupervisorRegistry:
    """Process-wide singleton holding one :class:`GatewaySupervisor` per Bot.

    Owns the shared :class:`LogTailer`, the dispatcher task that fans tailer
    output out to per-Bot inboxes, and the per-profile ``asyncio.Lock`` map
    that REST handlers use to serialize Start/Stop/Restart per Bot
    (GATEWAY-03 / D-14).
    """

    def __init__(self) -> None:
        self._supervisors: dict[str, GatewaySupervisor] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._shared_tailer: LogTailer | None = None
        self._tailer_task: asyncio.Task[None] | None = None
        self._dispatcher_task: asyncio.Task[None] | None = None

    # ---- lock map (GATEWAY-03) ----------------------------------------------

    def lock_for(self, bot_name: str) -> asyncio.Lock:
        """Return the per-Bot ``asyncio.Lock``, creating it on first use."""
        if bot_name not in self._locks:
            self._locks[bot_name] = asyncio.Lock()
        return self._locks[bot_name]

    # ---- supervisor map -----------------------------------------------------

    def get(self, bot_name: str) -> GatewaySupervisor | None:
        """Return the Supervisor for ``bot_name`` or ``None`` if not registered."""
        return self._supervisors.get(bot_name)

    def all_bots(self) -> list[str]:
        """Sorted list of bot names with a registered Supervisor."""
        return sorted(self._supervisors.keys())

    # ---- dispatcher loop ----------------------------------------------------

    async def _dispatcher_loop(self) -> None:
        """One consumer of the shared tailer; fans every line to all inboxes."""
        assert self._shared_tailer is not None
        try:
            async for line in self._shared_tailer.iter_lines():
                # Snapshot keys() to avoid 'dict changed size during iteration'
                # if a sibling task adds a Bot mid-flight (add_bot path).
                for sup in list(self._supervisors.values()):
                    sup.deliver(line)
        except asyncio.CancelledError:
            raise

    # ---- lifecycle ----------------------------------------------------------

    async def start_all(
        self,
        *,
        fs: ProfileFsAdapter,
        host: HostOps,
        log_path: Path,
        write_pairing: WritePairingFn,
    ) -> None:
        """Bootstrap: tailer + dispatcher + one Supervisor per .env-configured Bot.

        D-01: A Bot is "configured" iff its ``.env`` exists. Profiles without
        ``.env`` (incomplete onboarding) are skipped — the wizard's success
        path will register them via :meth:`add_bot` when ``.env`` lands.
        """
        # Stash deps so add_bot can re-use them mid-lifespan.
        self._fs = fs
        self._host = host
        self._write_pairing = write_pairing

        self._shared_tailer = LogTailer(log_path)
        self._tailer_task = asyncio.create_task(
            self._shared_tailer.run(), name="SharedGatewayLogTailer"
        )

        for name in await fs.list_profiles():
            if not await fs.host.path_exists(fs.env_path(name)):
                continue
            hub = BroadcastHub()
            sup = GatewaySupervisor(bot_name=name, hub=hub, write_pairing=write_pairing, host=host)
            sup.start()
            self._supervisors[name] = sup

        self._dispatcher_task = asyncio.create_task(
            self._dispatcher_loop(), name="SupervisorDispatcher"
        )

    async def add_bot(self, bot_name: str) -> None:
        """Register a Bot whose ``.env`` was just written (Wizard finish path).

        Reuses the dependencies passed to :meth:`start_all`. Idempotent —
        re-adding an existing Bot is a noop. Requires :meth:`start_all` to
        have run first (otherwise we have no shared tailer).
        """
        if bot_name in self._supervisors:
            return
        if self._shared_tailer is None:
            return
        hub = BroadcastHub()
        sup = GatewaySupervisor(
            bot_name=bot_name,
            hub=hub,
            write_pairing=self._write_pairing,
            host=self._host,
        )
        sup.start()
        self._supervisors[bot_name] = sup

    async def shutdown_all(self) -> None:
        """Cancel every Supervisor + the dispatcher + the tailer.

        D-03: NEVER calls ``hermes gateway stop`` / ``host.run_hermes`` here —
        Hermes processes are managed by launchd/systemd and must outlive the
        console. Acceptance: ``grep -E "gateway_stop|run_hermes"`` on this
        module returns 0.
        """
        # Cancel supervisors first so they stop draining their inboxes; the
        # dispatcher is harmless on cancellation but cleaner to stop in order.
        await asyncio.gather(
            *(s.shutdown() for s in self._supervisors.values()),
            return_exceptions=True,
        )
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._dispatcher_task, timeout=_SHUTDOWN_TIMEOUT_SEC)
        if self._shared_tailer is not None:
            self._shared_tailer.stop()
        if self._tailer_task is not None:
            self._tailer_task.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._tailer_task, timeout=_SHUTDOWN_TIMEOUT_SEC)
