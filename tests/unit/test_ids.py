"""UUIDv7 id generation — shape, ordering, and the 3.11 fallback path (SPEC-001 A1, A2)."""

import time
import uuid

import pytest

from mihomes.ids import _uuid7_fallback, new_id


def _assert_ordered_across_milliseconds(ids):
    """Byte-sort must match creation order for ids from *different* milliseconds.

    RFC 9562 orders v7 by its 48-bit unix_ts_ms prefix only. Within a single
    millisecond the remaining 74 bits are random (this implementation uses no
    monotonic counter — rand_a is random, not a sequence), so intra-millisecond
    order is deliberately unspecified. 1000 tight-loop calls land in 1-2
    milliseconds, which makes a naive `sorted(ids) == ids` assertion a coin flip.

    Comparing one id per distinct millisecond tests the guarantee that actually
    exists, and is what index locality on insert depends on.
    """
    first_per_ms = {}
    for i in ids:
        first_per_ms.setdefault(i.int >> 80, i)
    by_time = [first_per_ms[ms] for ms in sorted(first_per_ms)]
    assert sorted(by_time, key=lambda u: u.bytes) == by_time, (
        "ids from different milliseconds must byte-sort in creation order"
    )


def test_uuid7_properties():
    """A1 — ids are unique, time-ordered when sorted as bytes, and report version 7."""
    ids = [new_id() for _ in range(1000)]

    assert len(set(ids)) == 1000, "generated ids must be unique"
    assert all(i.version == 7 for i in ids), "every id must report version 7"

    # Time-ordering is the whole reason to pick v7 over v4: the 48-bit timestamp
    # occupies the most-significant bits, so byte-sort == creation order.
    _assert_ordered_across_milliseconds(ids)

    # And the prefix must genuinely advance over a sleep — proving the ordering
    # above comes from the clock, not from 1000 calls landing in one millisecond.
    early = new_id()
    time.sleep(0.005)
    late = new_id()
    assert early.bytes < late.bytes, "ids must order across a real time gap"


def test_fallback_generates_valid_v7():
    """A2 — the fallback works on the declared 3.11 floor, not only on 3.14+.

    `uuid.uuid7()` is stdlib from 3.14, but pyproject declares requires-python
    >=3.11, so the fallback is the code path that actually runs on the floor.
    It needs its own test regardless of which interpreter runs the suite.
    """
    ids = [_uuid7_fallback() for _ in range(1000)]

    assert len(set(ids)) == 1000
    assert all(i.version == 7 for i in ids)
    assert all(i.variant == uuid.RFC_4122 for i in ids), "variant must be RFC 4122"
    _assert_ordered_across_milliseconds(ids)

    early = _uuid7_fallback()
    time.sleep(0.005)
    late = _uuid7_fallback()
    assert early.bytes < late.bytes


def test_fallback_timestamp_is_current():
    """The fallback's 48-bit prefix must be a real unix_ts_ms, not zeros or garbage.

    Guards the bit-packing: a shift error still yields a well-formed UUID that
    passes the version check while sorting meaninglessly.
    """
    before = int(time.time() * 1000)
    got = _uuid7_fallback()
    after = int(time.time() * 1000)

    ts_ms = got.int >> 80
    assert before - 1000 <= ts_ms <= after + 1000, (
        f"timestamp prefix {ts_ms} is not within the call window "
        f"[{before}, {after}] — check the bit packing"
    )


@pytest.mark.parametrize("_run", range(5))
def test_fallback_bit_fields_are_masked(_run):
    """rand_a is 12 bits and rand_b is 62 bits; overflow would corrupt ver/variant."""
    got = _uuid7_fallback()

    assert (got.int >> 76) & 0xF == 0x7, "version nibble must be 7"
    assert (got.int >> 62) & 0b11 == 0b10, "variant bits must be 0b10"
