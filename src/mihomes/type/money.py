"""Money column type — store currency as exact integer cents.

Binary floats cannot represent most decimal money values exactly, so a column
of `Float` dollars accumulates rounding error the moment you sum or compare it
(the canonical `0.1 + 0.2 != 0.3`). We keep money on disk as an INTEGER number
of cents, which is exact under addition and comparison, and present it to the
application as float dollars so every existing read site keeps working.

The bind path rounds through `Decimal` rather than the builtin `round()` so that
half-cent inputs land on the right cent: `round(2.675, 2)` yields `2.67` because
2.675 is really 2.67499… in binary, whereas quantizing the *decimal* string of
the value gives the expected `2.68`.
"""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Integer
from sqlalchemy.types import TypeDecorator

_CENT = Decimal("1")


class Money(TypeDecorator):
    """dollars (float, Python side) <-> integer cents (INTEGER, DB side)."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        # str(value) gives the shortest decimal that round-trips the float, so
        # Decimal sees "2.675" not "2.67499999..."; scale to cents, half-up.
        cents = (Decimal(str(value)) * 100).quantize(_CENT, rounding=ROUND_HALF_UP)
        return int(cents)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value / 100
