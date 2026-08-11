"""G-R4 gate: reconciliation migration (7514b34eed7b) safety.

Exercises the four R4 gates from tasks/build-loop.md against a *file-based*
(not :memory:) SQLite DB seeded with real rows, because Alembic batch-mode
recreates each table and copies rows through the new schema — a hazard that
never shows up on an empty in-memory DB.

  G-R4a  round-trip: upgrade head -> downgrade -1 -> upgrade head, clean
  G-R4b  FK order: a deliberately-orphaned FK is nulled (not IntegrityError)
         even though db.py forces PRAGMA foreign_keys=ON on every connection
  G-R4c  data-preservation: enum defaults/data normalize to member NAMES,
         existing rows stay readable; H6 daily-recurrence rows actually flip
  G-R4d  autogenerate-clean: after head, autogenerate yields an EMPTY diff
"""

import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

import mihomes.models  # noqa: F401  (ensure every model is registered on Base)
from alembic import command
from mihomes.db import _get_alembic_dir
from mihomes.models import Base

PARENT = "424437e5c0ef"
RECONCILE = "7514b34eed7b"


def _cfg(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", _get_alembic_dir())
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _seed_at_parent(db_path: str) -> None:
    """Insert a minimal populated graph + a deliberate orphan, at parent rev.

    Uses raw SQL with foreign_keys=OFF so we can plant an orphan FK value that
    the reconciliation migration must clean before it creates the constraint.
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=OFF")
    c = con.cursor()
    # property + one real zone
    c.execute("INSERT INTO properties (name, slug, property_type, status, currency, "
              "occupied, created_at, updated_at) VALUES "
              "('Estate', 'estate', 'PRIMARY', 'OPEN', 'USD', 1, "
              "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
    prop = c.lastrowid
    c.execute("INSERT INTO zones (name, slug, property_id, created_at, updated_at) "
              "VALUES ('Zone A', 'zone-a', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (prop,))
    zone = c.lastrowid

    # A valid task (zone_id -> real zone) and an ORPHAN task (zone_id -> 99999)
    c.execute("INSERT INTO tasks (title, slug, property_id, zone_id, status, priority, "
              "created_at, updated_at) VALUES "
              "('Valid', 'valid', ?, ?, 'PENDING', 'MEDIUM', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
              (prop, zone))
    valid_task = c.lastrowid
    c.execute("INSERT INTO tasks (title, slug, property_id, zone_id, status, priority, "
              "created_at, updated_at) VALUES "
              "('Orphan', 'orphan', ?, 99999, 'PENDING', 'MEDIUM', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
              (prop,))
    orphan_task = c.lastrowid

    # H6 fixture: a 'Daily:%' task whose schedule is (wrongly) stored WEEKLY.
    c.execute("INSERT INTO tasks (title, slug, property_id, status, priority, "
              "created_at, updated_at) VALUES "
              "('Daily: sweep', 'daily-sweep', ?, 'PENDING', 'MEDIUM', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
              (prop,))
    daily_task = c.lastrowid
    c.execute("INSERT INTO task_schedules (task_id, frequency, created_at, updated_at) "
              "VALUES (?, 'WEEKLY', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (daily_task,))

    # M0/Q10 fixture: a book row created by the lowercase DB default 'good'.
    c.execute("INSERT INTO books (title, slug, property_id, condition, created_at, updated_at) "
              "VALUES ('B', 'b', ?, 'good', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (prop,))

    con.commit()
    con.close()
    return {"orphan_task": orphan_task, "valid_task": valid_task, "zone": zone}


@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "mihomes.db"
    url = f"sqlite:///{db_path}"
    cfg = _cfg(url)
    command.upgrade(cfg, PARENT)
    ids = _seed_at_parent(str(db_path))
    return {"url": url, "path": str(db_path), "cfg": cfg, "ids": ids}


def _q(db_path, sql, params=()):
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    row = con.execute(sql, params).fetchone()
    con.close()
    return row[0] if row else None


def test_g_r4b_orphan_cleaned_before_fk(seeded_db):
    """G-R4b: orphaned zone_id is nulled, migration does not IntegrityError."""
    command.upgrade(seeded_db["cfg"], RECONCILE)  # must not raise
    orphan = seeded_db["ids"]["orphan_task"]
    valid = seeded_db["ids"]["valid_task"]
    assert _q(seeded_db["path"], "SELECT zone_id FROM tasks WHERE id=?", (orphan,)) is None
    # the valid reference is preserved
    assert _q(seeded_db["path"], "SELECT zone_id FROM tasks WHERE id=?", (valid,)) == seeded_db["ids"]["zone"]


def test_g_r4b_fk_enforced_after_migration(seeded_db):
    """After the migration the FK is real: a fresh orphan insert is rejected."""
    command.upgrade(seeded_db["cfg"], RECONCILE)
    con = sqlite3.connect(seeded_db["path"])
    con.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO tasks (title, slug, property_id, zone_id, status, priority, "
                    "created_at, updated_at) VALUES "
                    "('Bad', 'bad', 1, 88888, 'PENDING', 'MEDIUM', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        con.commit()
    con.close()


def test_g_r4c_enum_defaults_normalized(seeded_db):
    """G-R4c: lowercase enum data/defaults become member NAMES; rows readable."""
    command.upgrade(seeded_db["cfg"], RECONCILE)
    # existing lowercase 'good' row normalized to 'GOOD'
    assert _q(seeded_db["path"], "SELECT condition FROM books WHERE slug='b'") == "GOOD"
    # column default now inserts the NAME form
    con = sqlite3.connect(seeded_db["path"])
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("INSERT INTO books (title, slug, property_id, created_at, updated_at) "
                "VALUES ('D', 'd', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
    con.commit()
    val = con.execute("SELECT condition FROM books WHERE slug='d'").fetchone()[0]
    con.close()
    assert val == "GOOD"


def test_g_r4c_h6_daily_recurrence_flips(seeded_db):
    """G-R4c/H6: a Daily:% schedule stored WEEKLY is corrected to DAILY."""
    command.upgrade(seeded_db["cfg"], RECONCILE)
    freq = _q(seeded_db["path"],
              "SELECT frequency FROM task_schedules ts JOIN tasks t ON t.id=ts.task_id "
              "WHERE t.slug='daily-sweep'")
    assert freq == "DAILY"


def test_g_r4a_round_trip(seeded_db):
    """G-R4a: upgrade -> downgrade -1 -> upgrade round-trips clean on real data."""
    command.upgrade(seeded_db["cfg"], RECONCILE)
    command.downgrade(seeded_db["cfg"], "-1")
    # after downgrade the H6 correction and enum-normalization are reversed
    assert _q(seeded_db["path"], "SELECT condition FROM books WHERE slug='b'") == "good"
    freq = _q(seeded_db["path"],
              "SELECT frequency FROM task_schedules ts JOIN tasks t ON t.id=ts.task_id "
              "WHERE t.slug='daily-sweep'")
    assert freq == "WEEKLY"
    command.upgrade(seeded_db["cfg"], RECONCILE)  # forward again, must not raise


@pytest.mark.skip(
    reason="SPEC-002 Step 6 replaces this tree. This oracle asserts the SQLite schema "
    "matches Base.metadata, which G2-G6 deliberately break across 37 tables "
    "(TenantOwned's account_id, per-account UNIQUE constraints, composite indexes and "
    "FKs, UUID PKs). Maintaining an exclusion list for a tree that 0001_pg_baseline "
    "archives to alembic/legacy_sqlite/ would grow with every group. G6.3 deletes this "
    "test with the revisions it checks. Expected-skip, declared in "
    "build-loop-spec002.md §0.1."
)
def test_g_r4d_autogenerate_empty(seeded_db):
    """G-R4d: after head, autogenerate produces an EMPTY diff (models==schema)."""
    command.upgrade(seeded_db["cfg"], "head")
    engine = create_engine(seeded_db["url"])

    # Mirror alembic/env.py's unmanaged-table filter (archive tables are raw-SQL
    # managed, not on Base.metadata, so autogenerate would perpetually drop them).
    # Also exclude `dummy`, a throwaway model that tests/unit/test_slug.py
    # registers on the shared Base.metadata — it is never migrated and would show
    # as phantom drift when the full suite runs before this test.
    # And `waitlist`, which IS on Base.metadata but is owned by the separate
    # alembic_landing/ tree (SPEC-001 D1/D3) — this tree never migrates it.
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
    assert diffs == [], f"schema drift remains after R4: {diffs}"
