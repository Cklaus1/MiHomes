"""H3: swapping the engine via db.get_engine(url) must also reset the cached
session factory, or get_session() silently keeps talking to the previous DB.

These tests poke the module globals directly, so they save and restore
db._engine / db._SessionLocal around each case — a sibling module owns a
module-scoped engine bound to the shared on-disk DB and must be left undisturbed
(see tasks/lessons.md on frozen config paths / engine collisions).
"""

import pytest
from sqlalchemy import text

import mihomes.db as db


@pytest.fixture
def saved_globals():
    prev_engine, prev_factory = db._engine, db._SessionLocal
    try:
        yield
    finally:
        db._engine, db._SessionLocal = prev_engine, prev_factory


def _make_db(url: str) -> None:
    """Create a one-column marker table so we can tell the two DBs apart."""
    eng = db.get_engine(url)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE marker (name TEXT)"))
        conn.execute(text("INSERT INTO marker (name) VALUES (:n)"), {"n": url})


def test_get_session_follows_engine_swap(tmp_path, saved_globals):
    url1 = f"sqlite:///{tmp_path / 'one.db'}"
    url2 = f"sqlite:///{tmp_path / 'two.db'}"

    # Build DB #1 and bind the session factory to it.
    _make_db(url1)
    factory1 = db.get_session_factory(db.get_engine(url1))
    with factory1() as s:
        assert s.execute(text("SELECT name FROM marker")).scalar() == url1

    # Build DB #2 (different table content) and swap the engine to it.
    _make_db(url2)
    db.get_engine(url2)  # swaps _engine — must invalidate the cached factory

    # get_session() with no explicit engine must now hit DB #2, not the stale one.
    with db.get_session() as s:
        assert s.execute(text("SELECT name FROM marker")).scalar() == url2


def test_get_engine_swap_resets_session_local(tmp_path, saved_globals):
    url1 = f"sqlite:///{tmp_path / 'a.db'}"
    url2 = f"sqlite:///{tmp_path / 'b.db'}"
    db.get_engine(url1)
    db.get_session_factory()  # populate _SessionLocal against engine #1
    assert db._SessionLocal is not None
    db.get_engine(url2)
    # The cached factory must be dropped so the next session rebinds to engine #2.
    assert db._SessionLocal is None
