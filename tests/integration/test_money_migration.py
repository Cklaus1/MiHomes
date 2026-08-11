"""R5.2 (M1): the money->int-cents migration preserves values exactly.

Mirrors the G-R4c data-preservation gate: seed real dollar rows at the revision
*before* the money migration, upgrade to head, and assert every value survives
the Float->Integer-cents cast without drift. Also exercises the downgrade so the
round-trip is proven, and re-checks the autogenerate oracle stays empty.
"""

import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

import mihomes.models  # noqa: F401
from alembic import command
from mihomes.models import Base
from tests.integration.test_migration_reconciliation import _cfg

MONEY_REV = "b3f5c1d9a72e"
PREV = "ce1a992f291e"


@pytest.fixture
def db_before_money(tmp_path):
    db_path = tmp_path / "mihomes.db"
    url = f"sqlite:///{db_path}"
    cfg = _cfg(url)
    command.upgrade(cfg, PREV)  # everything up to but not including the money cast
    return {"url": url, "path": str(db_path), "cfg": cfg}


def _seed_money(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO properties (name, slug, property_type, status, currency, "
                "occupied, created_at, updated_at) VALUES "
                "('P','p','PRIMARY','OPEN','USD',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    # a budget and a transaction with awkward cents that would drift as floats
    con.execute("INSERT INTO budgets (property_id, category, period, period_start, amount, "
                "currency, created_at, updated_at) VALUES "
                "(1,'Utilities','MONTHLY','2026-01-01',1234.56,'USD',"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    con.execute("INSERT INTO transactions (amount, currency, property_id, category, date, "
                "source, created_at, updated_at) VALUES "
                "(19.99,'USD',1,'Repairs','2026-01-02','manual',"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    # a NULL money value must stay NULL
    con.execute("INSERT INTO events (title, slug, property_id, event_date, budget, "
                "currency, status, created_at, updated_at) VALUES "
                "('Gala','gala',1,'2026-06-01',NULL,'USD','PLANNING',"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    con.commit()
    con.close()


def _raw(path, sql):
    con = sqlite3.connect(path)
    val = con.execute(sql).fetchone()[0]
    con.close()
    return val


def test_money_cast_preserves_values(db_before_money):
    path = db_before_money["path"]
    _seed_money(path)
    command.upgrade(db_before_money["cfg"], MONEY_REV)

    # raw on-disk values are now exact integer cents
    assert _raw(path, "SELECT amount FROM budgets WHERE category='Utilities'") == 123456
    assert _raw(path, "SELECT amount FROM transactions WHERE category='Repairs'") == 1999
    assert _raw(path, "SELECT budget FROM events WHERE slug='gala'") is None


def test_money_round_trip(db_before_money):
    """upgrade -> downgrade restores the original float dollars."""
    path = db_before_money["path"]
    _seed_money(path)
    command.upgrade(db_before_money["cfg"], MONEY_REV)
    command.downgrade(db_before_money["cfg"], PREV)
    assert _raw(path, "SELECT amount FROM budgets WHERE category='Utilities'") == 1234.56
    assert _raw(path, "SELECT amount FROM transactions WHERE category='Repairs'") == 19.99
    assert _raw(path, "SELECT budget FROM events WHERE slug='gala'") is None


@pytest.mark.skip(
    reason="SPEC-002 Step 6 replaces this tree — see the twin skip in "
    "test_migration_reconciliation.py. The money cast itself is still verified by "
    "test_money_cast_preserves_values and test_money_round_trip, which assert "
    "behaviour rather than schema shape and remain green. Expected-skip, declared in "
    "build-loop-spec002.md §0.1; G6.3 deletes it."
)
def test_autogenerate_empty_after_money(db_before_money):
    """G-R4d oracle: models and schema agree after the money migration."""
    command.upgrade(db_before_money["cfg"], "head")
    engine = create_engine(db_before_money["url"])
    # `waitlist` is on Base.metadata but owned by the separate alembic_landing/
    # tree (SPEC-001 D1/D3); this tree never migrates it. Same rationale as `dummy`.
    _unmanaged = {"audit_log_archive", "ai_conversations_archive", "dummy", "waitlist"}

    def include_object(obj, name, type_, reflected, compare_to):
        if type_ == "table" and name in _unmanaged:
            return False
        if type_ == "index" and getattr(obj, "table", None) is not None \
                and obj.table.name in _unmanaged:
            return False
        return True

    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "render_as_batch": True,
                "include_object": include_object,
                "target_metadata": Base.metadata,
            },
        )
        diffs = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert diffs == [], f"schema drift after money migration: {diffs}"
