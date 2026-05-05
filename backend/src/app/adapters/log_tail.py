"""LogTailer — Phase 2 SKELETON. Phase 4 wires WebSocket fanout.

Polls a log file (re-opens on inode change to survive log rotation).
Bounded ``asyncio.Queue`` with **drop-newest** semantics on overflow:
once full, new lines are silently dropped so we preserve the first ``queue_size``
lines of any flood — better for forensics than ring-buffer LRU.

**Anti-pattern: do NOT use** ``tail -F`` **subprocess** — log rotation breaks
its inode tracking, it's platform-specific, and CLAUDE.md explicitly bans it.
We poll at 250ms cadence which is adequate for ≤10 bots and avoids any
external process lifecycle.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path


class LogTailer:
    """Polling-based file tailer with bounded asyncio queue.

    Phase 2 ships only the skeleton (interface + polling + queue). Phase 4
    will wire ``iter_lines()`` to a WebSocket fanout for the live log viewer.
    """

    def __init__(
        self,
        log_path: Path,
        *,
        queue_size: int = 1000,
        poll_ms: int = 250,
        start_at_end: bool = False,
    ) -> None:
        self.log_path = log_path
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self.poll_ms = poll_ms
        self.start_at_end = start_at_end
        self._stop = False
        self._inode: int | None = None
        self._offset: int = 0

    async def run(self) -> None:
        """Main poll loop. Cancellation-safe (re-raises CancelledError).

        Sleeps ``poll_ms`` between polls. Stops when ``self.stop()`` is called
        or the surrounding task is cancelled.
        """
        try:
            while not self._stop:
                await self._poll_once()
                await asyncio.sleep(self.poll_ms / 1000)
        except asyncio.CancelledError:
            raise

    async def _poll_once(self) -> None:
        """Read any new bytes since last poll. Inode-aware (rotation-safe).

        - Missing file: noop, reset state so next appearance starts from 0.
        - New inode (rotation): reset offset to 0, read from start of new file.
        - Same inode, file shrunk: noop (we don't backfill historical content).
        - Same inode, file grew: read the new bytes, decode, enqueue lines.
        """
        try:
            stat = self.log_path.stat()
        except FileNotFoundError:
            self._inode = None
            self._offset = 0
            return
        if self._inode is None or stat.st_ino != self._inode:
            # First open or rotation — reset to start of (possibly new) file.
            self._inode = stat.st_ino
            self._offset = stat.st_size if self.start_at_end else 0
            # Only the first open should skip existing bytes. If the file
            # rotates while we are watching it, read the new file from start.
            self.start_at_end = False
        if stat.st_size <= self._offset:
            return
        # Read new bytes synchronously — file I/O is cheap relative to poll cadence.
        with open(self.log_path, "rb") as f:
            f.seek(self._offset)
            chunk = f.read(stat.st_size - self._offset)
            self._offset = f.tell()
        for raw_line in chunk.splitlines():
            line = raw_line.decode("utf-8", errors="replace")
            # Drop-NEWEST semantics: skip enqueue when full so the first
            # `queue_size` lines after a flood survive (forensic-friendly).
            if not self.queue.full():
                self.queue.put_nowait(line)

    async def iter_lines(self) -> AsyncIterator[str]:
        """Async iterator — yields each enqueued line in FIFO order.

        Phase 4 fans this out to per-connection WebSocket sinks. For now
        callers consume directly (e.g. tests).
        """
        while not self._stop:
            line = await self.queue.get()
            yield line

    def stop(self) -> None:
        """Signal the run loop to exit at next poll boundary."""
        self._stop = True
