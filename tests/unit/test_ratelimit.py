"""In-process per-IP token bucket (SPEC-001 A13, D10).

D10 picks in-process over Redis: the endpoints being protected are public,
unauthenticated, and write rows plus send email, but Phase 0 traffic does not
justify another moving part.
"""

import pytest

from mihomes.landing.ratelimit import TokenBucket


def test_burst_is_limited():
    """A13 — past the threshold the bucket refuses."""
    bucket = TokenBucket(capacity=3, refill_per_second=0.0)

    assert bucket.allow("1.2.3.4") is True
    assert bucket.allow("1.2.3.4") is True
    assert bucket.allow("1.2.3.4") is True
    assert bucket.allow("1.2.3.4") is False, "the 4th request in a burst of 3 must be refused"


def test_per_ip_isolation():
    """One abusive client must not lock out everyone else.

    A global counter would turn a single script into a denial of service against
    the launch page.
    """
    bucket = TokenBucket(capacity=2, refill_per_second=0.0)

    assert bucket.allow("10.0.0.1") is True
    assert bucket.allow("10.0.0.1") is True
    assert bucket.allow("10.0.0.1") is False

    assert bucket.allow("10.0.0.2") is True, "a different IP has its own budget"


def test_refill_restores_capacity():
    """Tokens come back over time — the limit is a rate, not a lifetime quota."""
    clock = {"now": 1000.0}
    bucket = TokenBucket(capacity=2, refill_per_second=1.0, clock=lambda: clock["now"])

    assert bucket.allow("ip") is True
    assert bucket.allow("ip") is True
    assert bucket.allow("ip") is False

    clock["now"] += 2.0
    assert bucket.allow("ip") is True, "two seconds at 1/s must restore two tokens"


def test_refill_does_not_exceed_capacity():
    """A long idle period must not bank unlimited burst."""
    clock = {"now": 0.0}
    bucket = TokenBucket(capacity=2, refill_per_second=1.0, clock=lambda: clock["now"])

    clock["now"] += 10_000.0
    assert bucket.allow("ip") is True
    assert bucket.allow("ip") is True
    assert bucket.allow("ip") is False, "capacity is the ceiling regardless of idle time"


def test_unknown_ip_is_allowed_first_time():
    bucket = TokenBucket(capacity=1, refill_per_second=0.0)
    assert bucket.allow("brand-new") is True


@pytest.mark.parametrize("capacity", [0, -1])
def test_capacity_must_be_positive(capacity):
    """A zero-capacity bucket would refuse the very first legitimate signup."""
    with pytest.raises(ValueError):
        TokenBucket(capacity=capacity, refill_per_second=1.0)


def test_bucket_does_not_grow_without_bound():
    """Per-IP state must be evictable, or a spoofed-IP flood becomes a memory leak.

    The bucket is in-process (D10) and public-facing, so unbounded key growth is
    the obvious way to knock the app over without tripping the rate limit itself.
    """
    bucket = TokenBucket(capacity=1, refill_per_second=1.0, max_tracked=50)
    for i in range(500):
        bucket.allow(f"10.0.{i // 256}.{i % 256}")

    assert len(bucket._buckets) <= 50, "tracked IPs must be capped"
