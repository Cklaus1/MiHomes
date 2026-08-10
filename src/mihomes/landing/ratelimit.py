"""In-process per-IP token bucket for the public landing endpoints (D10).

D10 chooses in-process over Redis deliberately: `POST /waitlist` and the OAuth
callback are public, unauthenticated, write rows and send email — but Phase 0
traffic does not justify another moving part.

Two consequences of "in-process" that the implementation has to respect:

- **Per-IP, not global.** A global counter would let one script deny the launch
  page to everyone.
- **Bounded state.** Per-IP keys in a public-facing dict are a memory leak under a
  spoofed-source flood, which is a way to take the app down *without* tripping the
  rate limit. Tracked IPs are capped and evicted oldest-first.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

__all__ = ["TokenBucket", "client_ip"]


def client_ip(request) -> str:
    """Client IP, honouring Fly's proxy headers.

    Lives here rather than in app.py so both the rate-limit middleware and the
    signup handler (which records `signup_ip`) use the same definition — and so
    importing it from routes.py does not create a cycle through the app factory.

    Behind Fly every connection appears to come from the proxy, so keying a bucket
    on `request.client.host` would collapse every visitor into ONE shared bucket:
    a single script could then deny the launch page to everyone.
    """
    forwarded = request.headers.get("fly-client-ip") or request.headers.get(
        "x-forwarded-for"
    )
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

# Defaults for POST /waitlist: a handful of attempts, then a slow drip. Generous
# enough for a person who mistypes their address twice, tight enough that
# scripted signup floods stop being free.
DEFAULT_CAPACITY = 5
DEFAULT_REFILL_PER_SECOND = 0.2   # one token every 5s
DEFAULT_MAX_TRACKED = 10_000


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class TokenBucket:
    """Classic token bucket, keyed by client IP."""

    def __init__(
        self,
        capacity: int = DEFAULT_CAPACITY,
        refill_per_second: float = DEFAULT_REFILL_PER_SECOND,
        *,
        max_tracked: int = DEFAULT_MAX_TRACKED,
        clock=time.monotonic,
    ) -> None:
        if capacity <= 0:
            # A zero-capacity bucket refuses the first legitimate signup, which
            # would silently break the funnel rather than protect it.
            raise ValueError("capacity must be positive")
        if refill_per_second < 0:
            raise ValueError("refill_per_second must not be negative")

        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.max_tracked = max_tracked
        self._clock = clock
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def allow(self, key: str) -> bool:
        """Consume a token for `key`. False when the caller is over its limit."""
        now = self._clock()
        bucket = self._buckets.get(key)

        if bucket is None:
            bucket = _Bucket(tokens=float(self.capacity), last_refill=now)
            self._buckets[key] = bucket
            self._evict_if_needed()
        else:
            # Refill for elapsed time, capped at capacity so a long idle period
            # cannot bank unlimited burst.
            elapsed = max(0.0, now - bucket.last_refill)
            bucket.tokens = min(
                float(self.capacity), bucket.tokens + elapsed * self.refill_per_second
            )
            bucket.last_refill = now
            self._buckets.move_to_end(key)

        if bucket.tokens < 1.0:
            return False

        bucket.tokens -= 1.0
        return True

    def _evict_if_needed(self) -> None:
        while len(self._buckets) > self.max_tracked:
            self._buckets.popitem(last=False)   # oldest-touched first
