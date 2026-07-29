"""R5.1 (M1/Q2): the Money TypeDecorator stores exact integer cents.

Money persists as INTEGER cents so that summation and comparison never drift
the way binary Float dollars do (0.1 + 0.2 != 0.3). The Python-facing value
stays float dollars — every read site keeps working unchanged — but the value
on disk is an exact integer, and the bind path rounds through Decimal so
half-cent inputs land on the correct cent (the classic 2.675 float trap).
"""

import sqlalchemy as sa
from sqlalchemy import text

from mihomes.type.money import Money


def _table_engine():
    engine = sa.create_engine("sqlite://")  # in-memory, per-test
    md = sa.MetaData()
    t = sa.Table(
        "money_probe",
        md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("amount", Money, nullable=True),
    )
    md.create_all(engine)
    return engine, t


def test_stored_as_integer_cents():
    """The raw column value on disk is an INTEGER number of cents."""
    engine, t = _table_engine()
    with engine.begin() as conn:
        conn.execute(t.insert(), {"id": 1, "amount": 19.99})
        # read the RAW stored value, bypassing the type decorator
        raw = conn.execute(text("SELECT amount FROM money_probe WHERE id=1")).scalar()
    assert raw == 1999
    assert isinstance(raw, int)


def test_round_trip_exact():
    """dollars -> cents -> dollars returns the identical dollar value."""
    engine, t = _table_engine()
    values = [0.0, 0.01, 0.99, 1.0, 19.99, 100.0, 1234.56, 999999.99]
    with engine.begin() as conn:
        for i, v in enumerate(values):
            conn.execute(t.insert(), {"id": i, "amount": v})
        for i, v in enumerate(values):
            got = conn.execute(t.select().where(t.c.id == i)).one().amount
            assert got == v, f"{v!r} round-tripped to {got!r}"


def test_no_accumulation_drift():
    """Summing stored cents is exact where summing floats would drift."""
    engine, t = _table_engine()
    with engine.begin() as conn:
        for i in range(10):
            conn.execute(t.insert(), {"id": i, "amount": 0.1})
        total_cents = conn.execute(text("SELECT SUM(amount) FROM money_probe")).scalar()
    assert total_cents == 100  # 10 * 10 cents, exact — no 0.9999999 tail


def test_half_cent_rounds_correctly():
    """2.675 must store as 268 cents, not 267 (the float round() trap)."""
    engine, t = _table_engine()
    with engine.begin() as conn:
        conn.execute(t.insert(), {"id": 1, "amount": 2.675})
        raw = conn.execute(text("SELECT amount FROM money_probe WHERE id=1")).scalar()
    assert raw == 268


def test_none_passes_through():
    """NULL money stays NULL in both directions (nullable columns)."""
    engine, t = _table_engine()
    with engine.begin() as conn:
        conn.execute(t.insert(), {"id": 1, "amount": None})
        got = conn.execute(t.select().where(t.c.id == 1)).one().amount
    assert got is None
