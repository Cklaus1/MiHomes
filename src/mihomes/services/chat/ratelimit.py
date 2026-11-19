"""Per-membership chat rate limits (SPEC-014 D8).

Keyed on the membership, never the IP: on evo-dev every member arrives through Tailscale Serve
from one address (N6). In-process, so with N processes the effective limit is N× and a restart
clears it (U4) — acceptable for flood control, not a security boundary.

Neither existing limiter fits (§0.4): `auth.ratelimit` counts failures, and importing
`landing.ratelimit` would load the landing app, which SPEC-001 keeps separate.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque

__all__ = ["POSTS", "REACTIONS", "SlidingWindow"]


class SlidingWindow:
    def __init__(self, limit: int, window_s: float, *, max_keys: int = 10_000,
                 clock=time.monotonic) -> None:
        self.limit, self.window_s, self.max_keys, self._clock = limit, window_s, max_keys, clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """Record one event. None = allowed; otherwise the seconds until a slot frees (for
        Retry-After). A refused hit is NOT recorded, so hammering does not extend the lockout."""
        now = self._clock()
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                q = self._hits[key] = deque()
                while len(self._hits) > self.max_keys:     # LRU-bounded memory
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(key)
            while q and q[0] <= now - self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return max(q[0] + self.window_s - now, 0.001)
            q.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


POSTS = SlidingWindow(20, 60.0)        # posts and edits share it
REACTIONS = SlidingWindow(10, 10.0)
# The 4000-character body limit is SPEC-012's posts.MAX_MEMBER_BODY (+ the DB CHECK).
