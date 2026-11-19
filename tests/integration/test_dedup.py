"""Gateway dedup store — M22 (one store per gateway) + M23 (insertion-order prune).

M22: the monitor and the extractor each kept their OWN processed-id store
(a DB key vs. a sidecar JSON file), so a message handled by one was invisible
to the other → duplicate issues/tasks. There must be exactly one store per
gateway, shared by both pollers. The concurrent-poll offset race is guarded by
an advisory lease so only one poller advances the offset at a time.

M23: pruning did `list(set)[-N:]` — a set has arbitrary order, so the "kept"
ids were random and recent ids could be evicted. Pruning must be
insertion-ordered and drop from the FRONT (oldest first).
"""

import os

import pytest

from mihomes import db
from mihomes.services.gateways.dedup import ProcessedIdStore, poll_lease
from mihomes.tenancy import account_context


@pytest.fixture
def isolated_db(account_a):
    """Real Postgres (SPEC-002 §6 Step 15), scoped to a fresh account.

    `ProcessedIdStore`/`poll_lease` go through `get_config`/`set_config`, and `Configuration` is
    `TenantOwned` (G6.1 gave it a composite `(account_id, key)` primary key) — so a call with no
    account bound raises `LookupError`, same as any other tenant-owned write. This fixture used to
    build its own throwaway SQLite file and call `db.init_db()` directly; `init_db()` now refuses
    SQLite outright (`UnsupportedBackendError`, G6.2), which is what actually broke this file, not
    the tenancy requirement alone. `account_a` (conftest.py) is a fresh account per test, so one
    test's `gateway.telegram.processed_ids` key is invisible to the next — no explicit cleanup
    needed, unlike `test_ops_commands.py`'s shared-account tables.

    **Explicitly rebinds `mihomes.db`'s global engine to `TEST_DATABASE_URL`** rather than trusting
    the ambient `DATABASE_URL` env var. `account_a` always creates its account in `TEST_DATABASE_URL`
    (via `_pg_engine`, a fixed engine object), but `ProcessedIdStore`/`poll_lease` read `DATABASE_URL`
    through `mihomes.db.get_session()` — and `cli_database` (conftest.py, session-scoped) repoints
    that env var at a *different* dedicated database the moment any of its five consumer modules
    runs, restoring it only at session teardown. Collected alphabetically, several of those modules
    run before this file, so trusting the ambient value here silently wrote into the wrong database
    (`ForeignKeyViolation: account_id ... is not present`) — same account, wrong database.
    """
    test_url = os.environ["TEST_DATABASE_URL"]
    prev_engine, prev_factory = db._engine, db._SessionLocal
    my_engine = db.get_engine(test_url)
    try:
        with account_context(account_a):
            yield
    finally:
        my_engine.dispose()
        db._engine, db._SessionLocal = prev_engine, prev_factory


# ------------------------------------------------------------------ M22: one store


def test_monitor_and_extractor_share_one_store(isolated_db):
    """Two handles on the same gateway key see each other's ids."""
    monitor = ProcessedIdStore("gateway.telegram.processed_ids")
    extractor = ProcessedIdStore("gateway.telegram.processed_ids")

    monitor.add(["a:1", "a:2"])

    assert "a:1" in extractor.load()
    # extractor adds more; monitor sees them too
    extractor.add(["b:9"])
    assert "b:9" in monitor.load()


def test_contains_after_reload(isolated_db):
    store = ProcessedIdStore("gateway.whatsapp.processed_ids")
    store.add(["m1", "m2"])
    assert store.contains("m1") and store.contains("m2")
    assert not store.contains("nope")


# ------------------------------------------------------------------ M23: prune order


def test_prune_keeps_most_recent_insertion_ordered(isolated_db):
    store = ProcessedIdStore("gateway.telegram.processed_ids", cap=100)
    # add 250 ids in a known order
    store.add([f"id{i}" for i in range(250)])

    kept = list(store.load())
    assert len(kept) == 100
    # the newest 100 survive (id150..id249), oldest dropped from the front
    assert kept == [f"id{i}" for i in range(150, 250)]


def test_add_is_idempotent_and_preserves_order(isolated_db):
    store = ProcessedIdStore("gateway.telegram.processed_ids", cap=10)
    store.add(["x", "y"])
    store.add(["x", "z"])  # re-adding x must not duplicate or reorder-explode
    kept = list(store.load())
    assert kept == ["x", "y", "z"]


# ------------------------------------------------------------------ M22: poll lease


def test_poll_lease_is_exclusive_while_held(isolated_db):
    with poll_lease("telegram", ttl_seconds=120) as got_first:
        assert got_first is True
        # a second poller cannot acquire while the first holds a fresh lease
        with poll_lease("telegram", ttl_seconds=120) as got_second:
            assert got_second is False


def test_poll_lease_reacquirable_after_release(isolated_db):
    with poll_lease("telegram") as a:
        assert a is True
    with poll_lease("telegram") as b:
        assert b is True
