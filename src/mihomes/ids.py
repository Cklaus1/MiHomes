"""UUIDv7 generation — one helper so every id in the system is time-ordered."""

from __future__ import annotations

import os
import time
import uuid

__all__ = ["new_id"]


def _uuid7_fallback() -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit unix_ts_ms | ver | rand_a | var | rand_b."""
    ts_ms = int(time.time() * 1000) & 0xFFFF_FFFF_FFFF
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = (rand >> 62) & 0x0FFF          # 12 bits
    rand_b = rand & 0x3FFF_FFFF_FFFF_FFFF   # 62 bits
    value = (
        (ts_ms << 80)
        | (0x7 << 76)        # version 7
        | (rand_a << 64)
        | (0b10 << 62)       # variant RFC 4122
        | rand_b
    )
    return uuid.UUID(int=value)


# uuid.uuid7() is stdlib from Python 3.14; pyproject declares requires-python >=3.11.
# Bind once at import so the per-call path stays a plain function reference.
new_id = getattr(uuid, "uuid7", _uuid7_fallback)
