"""Caps on open chat streams (SPEC-014 D9).

Browsers allow six HTTP/1.1 connections per host, so a member with a seventh open chat tab would
starve every other request from that browser — and an unbounded process count is a cheap way to
exhaust a server. Over either cap the route answers 429 without opening the stream. EventSource
does not retry a non-200, and `chat.js` reopens with Last-Event-ID when a hidden tab returns.
"""

from __future__ import annotations

import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from typing import Iterator

__all__ = ["MAX_STREAMS_PER_MEMBER", "MAX_STREAMS_PER_PROCESS", "acquire", "open_counts",
           "release", "stream_slots"]

MAX_STREAMS_PER_MEMBER = 6
MAX_STREAMS_PER_PROCESS = 200

_lock = threading.Lock()
_per_member: Counter[uuid.UUID] = Counter()
_total = 0


def acquire(membership_id: uuid.UUID) -> bool:
    global _total
    with _lock:
        if _total >= MAX_STREAMS_PER_PROCESS or _per_member[membership_id] >= MAX_STREAMS_PER_MEMBER:
            return False
        _per_member[membership_id] += 1
        _total += 1
        return True


def release(membership_id: uuid.UUID) -> None:
    global _total
    with _lock:
        if _per_member[membership_id] > 0:
            _per_member[membership_id] -= 1
            _total -= 1
        if _per_member[membership_id] == 0:
            del _per_member[membership_id]


@contextmanager
def stream_slots(membership_id: uuid.UUID) -> Iterator[bool]:
    """Yield False when over a cap. The release is in `finally`, so a disconnect frees the slot."""
    got = acquire(membership_id)
    try:
        yield got
    finally:
        if got:
            release(membership_id)


def open_counts() -> tuple[int, int]:
    """(total open, members with at least one) — for tests and the load check."""
    with _lock:
        return _total, len(_per_member)
