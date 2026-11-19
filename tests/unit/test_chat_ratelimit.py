"""SPEC-014 D8/D9 units — the sliding window (injected clock, no sleeps) and the stream counters."""

from __future__ import annotations

import uuid

from mihomes.services.chat import streams
from mihomes.services.chat.ratelimit import SlidingWindow


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_sliding_window_limits_and_recovers():
    clock = Clock()
    w = SlidingWindow(3, 10.0, clock=clock)
    assert [w.hit("a") for _ in range(3)] == [None, None, None]
    wait = w.hit("a")
    assert wait is not None and 0 < wait <= 10
    assert w.hit("b") is None                       # per key
    clock.t += 4
    assert w.hit("a") is not None                   # refused hits are not recorded...
    clock.t += 6.01
    assert w.hit("a") is None                       # ...so the first slot frees on time


def test_keys_are_lru_bounded():
    w = SlidingWindow(1, 60.0, max_keys=2, clock=Clock())
    for k in ("a", "b", "c"):
        assert w.hit(k) is None
    assert w.hit("a") is None                       # "a" was evicted, so it starts fresh


def test_stream_slots_cap_and_release(monkeypatch):
    monkeypatch.setattr(streams, "MAX_STREAMS_PER_PROCESS", 8)
    a, b = uuid.uuid4(), uuid.uuid4()
    held = [streams.acquire(a) for _ in range(6)]
    assert held == [True] * 6 and streams.acquire(a) is False
    assert streams.acquire(b) and streams.acquire(b)
    assert streams.acquire(b) is False               # process cap (8) reached
    streams.release(a)
    assert streams.acquire(b) is True
    for _ in range(5):
        streams.release(a)
    for _ in range(3):
        streams.release(b)
    assert streams.open_counts() == (0, 0)
    with streams.stream_slots(a) as ok:
        assert ok and streams.open_counts() == (1, 1)
    assert streams.open_counts() == (0, 0)
