"""BroadcastHub — multi-subscriber fan-out for one Bot's gateway log.

One :class:`BroadcastHub` per Bot. Each WebSocket connection registers a
:class:`Subscriber` (with optional keyword + level filter); :meth:`publish`
fans the line out to every subscriber whose filter matches.

Backpressure (D-07 / GATEWAY-09):
    * Each subscriber has a bounded ``asyncio.Queue(maxsize=1000)``.
    * On overflow we **drop the newest** line and increment ``dropped_count``
      (the WS endpoint surfaces this to the client so the UI can render a
      "已丢弃 N 行" banner).
    * Slow subscribers MUST NOT block fast ones — :meth:`publish` is a sync,
      non-blocking method (Pitfall #4 in 04-RESEARCH.md).

Filtering happens here so the Supervisor only does it once per line per
subscriber (the alternative — filtering in the WS endpoint after the fan-out
queue — wastes queue slots on lines the client is going to drop anyway).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

# Per-subscriber queue cap. 1000 lines @ ~120 bytes ≈ 120KB per slow client —
# bounded RAM for ≤10 Bots * ≤4 viewers per Bot worst-case. Each Subscriber
# wraps an ``asyncio.Queue(maxsize=1000)`` (D-07 / GATEWAY-09 anchor; the
# literal is preserved in this comment for grep-traceability).
_SUBSCRIBER_QUEUE_MAXSIZE = 1000


@dataclass
class Subscriber:
    """One WebSocket connection's view onto a Bot's log stream.

    ``queue`` is exclusively read by the WS endpoint. ``dropped_count``
    increments when :meth:`BroadcastHub.publish` finds the queue full and
    drops the line — the WS endpoint reads the value when sending its next
    message envelope so the UI can show the drop counter without polling.
    """

    queue: asyncio.Queue[str]
    keywords: list[str] = field(default_factory=list)
    level_min: str | None = None
    dropped_count: int = 0


class BroadcastHub:
    """Single-Bot fan-out hub. One per Bot — owned by :class:`GatewaySupervisor`."""

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []

    @property
    def subscriber_count(self) -> int:
        """Number of currently registered subscribers (for diagnostics)."""
        return len(self._subs)

    def subscribe(self, *, keywords: list[str], level_min: str | None) -> Subscriber:
        """Register a new subscriber with optional keyword + level filter."""
        sub = Subscriber(
            queue=asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAXSIZE),
            keywords=list(keywords),
            level_min=level_min,
        )
        self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        """Remove ``sub``. Idempotent — re-removing a gone subscriber is a noop."""
        with contextlib.suppress(ValueError):
            self._subs.remove(sub)

    def publish(self, line: str) -> None:
        """Fan ``line`` out to every matching subscriber. Sync + non-blocking.

        Pitfall #4 (04-RESEARCH): NEVER make this async. The Supervisor calls
        publish on every line it absorbs; if publish awaited anywhere, a
        slow/missing subscriber could starve the whole Bot's pairing-extraction
        pipeline. Use ``put_nowait`` and drop on QueueFull.
        """
        for sub in self._subs:
            if not _matches(line, sub.keywords, sub.level_min):
                continue
            try:
                sub.queue.put_nowait(line)
            except asyncio.QueueFull:
                # Drop-newest backpressure: keep the existing 1000 lines for
                # forensics; the new line is the casualty. dropped_count is
                # surfaced to the UI by the WS endpoint.
                sub.dropped_count += 1


# Hermes log levels in priority order. ``level_min='warn'`` admits lines
# containing WARN | ERROR | CRITICAL but rejects DEBUG / INFO. Comparison is
# case-insensitive on the line content; missing-level lines are treated as
# INFO so they pass an INFO floor.
_LEVEL_ORDER: tuple[str, ...] = ("debug", "info", "warn", "warning", "error", "critical")
_LEVEL_RANK: dict[str, int] = {
    "debug": 0,
    "info": 1,
    "warn": 2,
    "warning": 2,
    "error": 3,
    "critical": 4,
}


def _matches(line: str, keywords: list[str], level_min: str | None) -> bool:
    """Return True iff ``line`` passes the keyword AND level filter."""
    if keywords and not any(k in line for k in keywords):
        return False
    if level_min:
        floor = _LEVEL_RANK.get(level_min.lower())
        if floor is None or floor <= _LEVEL_RANK["info"]:
            # Unknown level or 'info' floor — accept everything (info is the
            # default minimum anyway).
            return True
        upper = line.upper()
        # Accept iff the line carries any token at or above the floor.
        for token in _LEVEL_ORDER:
            rank = _LEVEL_RANK[token]
            if rank >= floor and token.upper() in upper:
                return True
        return False
    return True
