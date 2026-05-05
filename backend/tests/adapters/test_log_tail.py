"""Unit tests for LogTailer skeleton — Plan 02-03 Task 3.

Phase 4 will expand with WebSocket fanout tests; Phase 2 covers:
- Construction defaults
- Lines-flow-through correctness
- Missing-file tolerance
- Drop-newest overflow semantics (forensic preservation)
- Async iter_lines yields strings
- Cancellation cleanup
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.adapters.log_tail import LogTailer

# ---- Construction defaults ---------------------------------------------------


def test_logtailer_constructs_with_path_and_default_queue_size() -> None:
    """Defaults match the skeleton spec — Phase 4 may tune."""
    tailer = LogTailer(Path("/tmp/x.log"))
    assert tailer.queue.maxsize == 1000
    assert tailer.poll_ms == 250
    assert tailer.log_path == Path("/tmp/x.log")


# ---- Lines flow through ------------------------------------------------------


async def test_logtailer_lines_appended_appear_in_queue_within_one_second(
    tmp_path: Path,
) -> None:
    """Append two lines after starting tailer; both appear in queue order."""
    log_file = tmp_path / "live.log"
    log_file.write_text("")  # touch

    # Fast poll for the test (don't wait 250ms)
    tailer = LogTailer(log_file, poll_ms=20)
    task = asyncio.create_task(tailer.run())

    # Give run() one tick to register the (empty) initial state.
    await asyncio.sleep(0.05)
    log_file.write_text("hello\nworld\n")

    # Wait until both lines arrive (or 1s timeout)
    line1 = await asyncio.wait_for(tailer.queue.get(), timeout=1.0)
    line2 = await asyncio.wait_for(tailer.queue.get(), timeout=1.0)
    assert line1 == "hello"
    assert line2 == "world"

    tailer.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# ---- Missing file tolerance --------------------------------------------------


async def test_logtailer_handles_missing_file_gracefully(tmp_path: Path) -> None:
    """Tailer on non-existent path doesn't raise; once file appears, lines flow."""
    log_file = tmp_path / "appears-later.log"
    assert not log_file.exists()

    tailer = LogTailer(log_file, poll_ms=20)
    task = asyncio.create_task(tailer.run())

    # Run for ~50ms with no file — must not raise
    await asyncio.sleep(0.05)
    assert not task.done()  # still polling, no exception

    # Now create the file
    log_file.write_text("first-line\n")
    line = await asyncio.wait_for(tailer.queue.get(), timeout=1.0)
    assert line == "first-line"

    tailer.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# ---- Drop semantics on overflow ----------------------------------------------


async def test_logtailer_queue_drops_newest_on_overflow(tmp_path: Path) -> None:
    """queue_size=3, append 5 lines, no consumer → only first 3 in queue.

    DROP-NEWEST semantics (documented in code): preserves the FIRST 3 lines
    after a flood, which is what user wants for forensics.
    """
    log_file = tmp_path / "flood.log"
    log_file.write_text("L1\nL2\nL3\nL4\nL5\n")

    tailer = LogTailer(log_file, queue_size=3, poll_ms=20)
    # Drive a single poll directly; no consumer.
    await tailer._poll_once()

    # Queue should hold exactly 3 entries (the FIRST 3, since we drop newest).
    assert tailer.queue.qsize() == 3
    assert tailer.queue.get_nowait() == "L1"
    assert tailer.queue.get_nowait() == "L2"
    assert tailer.queue.get_nowait() == "L3"
    assert tailer.queue.empty()


# ---- iter_lines yields strings -----------------------------------------------


async def test_logtailer_iter_lines_yields_strings(tmp_path: Path) -> None:
    """async for x in tailer.iter_lines() yields str. Confirms protocol."""
    log_file = tmp_path / "iter.log"
    log_file.write_text("alpha\nbeta\n")

    tailer = LogTailer(log_file, poll_ms=20)
    task = asyncio.create_task(tailer.run())

    received: list[str] = []

    async def consume() -> None:
        async for line in tailer.iter_lines():
            received.append(line)
            if len(received) == 2:
                tailer.stop()
                break

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(consumer, timeout=2.0)

    assert received == ["alpha", "beta"]
    assert all(isinstance(line, str) for line in received)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# ---- Cancellation cleanliness ------------------------------------------------


async def test_logtailer_run_exits_cleanly_on_cancel(tmp_path: Path) -> None:
    """task.cancel() while in run() propagates CancelledError; no leaks."""
    log_file = tmp_path / "cancel.log"
    log_file.write_text("")
    tailer = LogTailer(log_file, poll_ms=50)

    task = asyncio.create_task(tailer.run())
    await asyncio.sleep(0.02)  # let run() enter the loop
    task.cancel()
    results = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)


async def test_logtailer_start_at_end_ignores_existing_lines_then_yields_appends(
    tmp_path: Path,
) -> None:
    """A live viewer can load history separately, then tail only new lines."""
    log_file = tmp_path / "gateway.log"
    log_file.write_text("old line\n", encoding="utf-8")

    tailer = LogTailer(log_file, poll_ms=20, start_at_end=True)
    task = asyncio.create_task(tailer.run())

    await asyncio.sleep(0.05)
    assert tailer.queue.empty()

    with open(log_file, "a", encoding="utf-8") as f:
        f.write("new line\n")

    line = await asyncio.wait_for(tailer.queue.get(), timeout=1.0)
    assert line == "new line"

    tailer.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
