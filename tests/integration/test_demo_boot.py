"""Regression: demo mode (`--demo` / `dev --demo`) must boot without crashing.

Bug (spec D5): `_seed_demo_db()` builds demo.db via `Base.metadata.create_all()`
without stamping `alembic_version`, then `init_db()` runs `alembic upgrade head`
on the same file — the first migration re-creates `audit_log` and SQLite raises
`OperationalError: table audit_log already exists`. Demo mode could never start.
"""

import os
import tempfile

# Override MIHOMES_DIR before importing anything that reads config paths.
_test_dir = tempfile.mkdtemp()
os.environ.setdefault("MIHOMES_DIR", _test_dir)

import mihomes.db as db
from mihomes.config import DB_DIR, ensure_dirs
from mihomes.web.server import _seed_demo_db


def _reset_demo_state(monkeypatch):
    """Fresh process simulation: no cached engine, no stale demo.db."""
    monkeypatch.setenv("MIHOMES_DEMO", "1")
    ensure_dirs()  # main()/dev() do this before _seed_demo_db()
    db._engine = None
    db._SessionLocal = None
    for suffix in ("", "-wal", "-shm"):
        p = DB_DIR / f"demo.db{suffix}"
        if p.exists():
            p.unlink()


def test_demo_boot_seed_then_init_does_not_crash(monkeypatch):
    """The exact main()/dev() demo sequence: seed, then init_db()."""
    _reset_demo_state(monkeypatch)

    _seed_demo_db()          # create_all + load demo data
    db.init_db()             # <-- crashed here pre-fix (table already exists)

    # And the seeded data is actually reachable through the migrated engine.
    from mihomes.models.property import Property
    with db.get_session() as session:
        assert session.query(Property).count() > 0


def test_demo_boot_is_idempotent(monkeypatch):
    """Booting demo twice must not crash or duplicate the schema."""
    _reset_demo_state(monkeypatch)
    _seed_demo_db()
    db.init_db()

    # Second boot in the same (now-migrated) demo.db.
    db._engine = None
    db._SessionLocal = None
    _seed_demo_db()
    db.init_db()
