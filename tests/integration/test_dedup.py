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

import pytest

from mihomes import db
from mihomes.services.gateways.dedup import ProcessedIdStore, poll_lease


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    from mihomes import config

    db_path = tmp_path / "dedup.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{db_path}")
    prev = (db._engine, db._SessionLocal)
    db._engine = db._SessionLocal = None
    db.init_db(url=f"sqlite:///{db_path}")
    try:
        yield
    finally:
        db.dispose_engine()
        db._engine, db._SessionLocal = prev


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
