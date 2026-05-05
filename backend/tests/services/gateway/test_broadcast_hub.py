"""Unit tests for broadcast_hub — Phase 4 GATEWAY-09 / D-05 / D-07."""

from __future__ import annotations

import asyncio

import pytest

from app.services.gateway.broadcast_hub import BroadcastHub


async def test_h1_subscribe_publish_basic() -> None:
    """H1 — single subscriber receives a published line."""
    hub = BroadcastHub()
    sub = hub.subscribe(keywords=[], level_min=None)
    hub.publish("hi")
    line = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert line == "hi"


async def test_h2_two_subscribers_both_receive() -> None:
    """H2 — fan-out: every subscriber sees every matching publish."""
    hub = BroadcastHub()
    sub_a = hub.subscribe(keywords=[], level_min=None)
    sub_b = hub.subscribe(keywords=[], level_min=None)
    hub.publish("hello")
    a = await asyncio.wait_for(sub_a.queue.get(), timeout=1.0)
    b = await asyncio.wait_for(sub_b.queue.get(), timeout=1.0)
    assert a == "hello"
    assert b == "hello"


async def test_h3_keyword_filter_excludes_non_matches() -> None:
    """H3 — keyword filter is server-side (D-06)."""
    hub = BroadcastHub()
    sub = hub.subscribe(keywords=["error"], level_min=None)
    hub.publish("info: ok")
    hub.publish("error: bad")
    # Only the matching line should land.
    line = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert line == "error: bad"
    assert sub.queue.empty()


async def test_h4_drop_newest_backpressure() -> None:
    """H4 — overflow does NOT raise; dropped_count increments; existing lines remain."""
    hub = BroadcastHub()
    sub = hub.subscribe(keywords=[], level_min=None)
    # Fill the queue to its 1000-line cap.
    for i in range(1000):
        hub.publish(f"line-{i}")
    assert sub.queue.qsize() == 1000
    # 1001th publish must NOT raise.
    hub.publish("overflow")
    assert sub.dropped_count == 1
    assert sub.queue.qsize() == 1000
    # Existing 1000 items remain (line-0 ... line-999); the new one is dropped.
    first = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert first == "line-0"


async def test_h5_slow_subscriber_does_not_block_fast_one() -> None:
    """H5 — drops are independent: slow client's queue full, fast client's empty."""
    hub = BroadcastHub()
    slow = hub.subscribe(keywords=[], level_min=None)
    fast = hub.subscribe(keywords=[], level_min=None)
    # Saturate the slow subscriber.
    for i in range(1000):
        hub.publish(f"line-{i}")
    # Drain fast first — both queues filled to 1000 by the loop above.
    assert fast.queue.qsize() == 1000
    assert slow.queue.qsize() == 1000
    # Drain fast subscriber to make room for one more line.
    while not fast.queue.empty():
        fast.queue.get_nowait()
    # Publish one more — slow drops, fast accepts.
    hub.publish("after-drain")
    assert fast.queue.qsize() == 1
    assert slow.dropped_count == 1
    assert fast.dropped_count == 0


async def test_h6_unsubscribe_stops_delivery() -> None:
    """H6 — after unsubscribe, future publishes do not reach the gone subscriber."""
    hub = BroadcastHub()
    sub = hub.subscribe(keywords=[], level_min=None)
    hub.unsubscribe(sub)
    hub.publish("after-unsub")
    assert sub.queue.empty()


async def test_unsubscribe_is_idempotent() -> None:
    """Unsubscribing twice does not raise."""
    hub = BroadcastHub()
    sub = hub.subscribe(keywords=[], level_min=None)
    hub.unsubscribe(sub)
    hub.unsubscribe(sub)


async def test_publish_with_zero_subscribers_is_safe() -> None:
    """A publish with no subscribers is a noop — Supervisor calls publish even
    when no UI clients are connected (GATEWAY-04 / GATEWAY-10).
    """
    hub = BroadcastHub()
    # Must not raise.
    hub.publish("nobody listening")
    assert hub.subscriber_count == 0


async def test_level_min_filters_below_floor() -> None:
    """level_min=warn rejects INFO/DEBUG, accepts WARN/ERROR/CRITICAL."""
    hub = BroadcastHub()
    sub = hub.subscribe(keywords=[], level_min="warn")
    hub.publish("INFO: nothing happened")
    hub.publish("DEBUG: trace here")
    hub.publish("WARN: heads up")
    hub.publish("ERROR: kaboom")
    received: list[str] = []
    while not sub.queue.empty():
        received.append(sub.queue.get_nowait())
    assert received == ["WARN: heads up", "ERROR: kaboom"]


async def test_level_min_info_is_passthrough() -> None:
    """level_min='info' admits everything (info is the default minimum)."""
    hub = BroadcastHub()
    sub = hub.subscribe(keywords=[], level_min="info")
    hub.publish("INFO: a")
    hub.publish("DEBUG: b")
    received: list[str] = []
    while not sub.queue.empty():
        received.append(sub.queue.get_nowait())
    assert received == ["INFO: a", "DEBUG: b"]


async def test_publish_is_sync_not_async() -> None:
    """Pitfall #4 — publish must be synchronous so a slow subscriber can't block.

    This test is a compile-time-ish check: we call publish as a regular sync
    function and assert the return value is None. If publish were async this
    would assign a coroutine object instead.
    """
    hub = BroadcastHub()
    hub.subscribe(keywords=[], level_min=None)
    result = hub.publish("x")
    assert result is None
    # If publish were async we'd get a coroutine and pytest would warn.
    assert not asyncio.iscoroutine(result)


@pytest.mark.parametrize("size", [100, 500, 999])
async def test_subscriber_queue_size_under_cap(size: int) -> None:
    """Sanity: subscriber queue can hold up to 1000 items."""
    hub = BroadcastHub()
    sub = hub.subscribe(keywords=[], level_min=None)
    for i in range(size):
        hub.publish(f"l-{i}")
    assert sub.queue.qsize() == size
    assert sub.dropped_count == 0
