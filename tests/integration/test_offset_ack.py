"""M21 · poison-message guard — no loss, no hot-loop.

The monitor used to advance the Telegram offset BEFORE processing a batch, so a
crash mid-batch permanently lost those messages. The naive inverse ("process
first, then ack") deadlocks on a deterministically-crashing update: it refetches
→ crashes → refetches forever, blocking every later message.

The `PoisonGuard` breaks the tie: each id's attempt counter is persisted BEFORE
processing. A crashing id is retried up to `max_attempts` (no loss), then
quarantined so the offset can advance past it (no hot-loop). A successfully
processed id is cleared so its counter never leaks.
"""

import pytest

from mihomes import db
from mihomes.services.gateways.dedup import PoisonGuard


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    from mihomes import config

    db_path = tmp_path / "poison.db"
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


def test_crash_no_loss_no_hotloop(isolated_db):
    """A poison id is retried up to the cap, then quarantined — never lost, never looped."""
    guard = PoisonGuard("telegram", max_attempts=3)
    poison = "chat:99"

    # Simulate the monitor seeing the same crashing update on each restart.
    # Attempts 1 and 2: still live (must be retried → no message loss).
    for attempt in (1, 2):
        guard.mark_attempt([poison])
        assert not guard.is_quarantined(poison), f"attempt {attempt} should still retry"

    # Attempt 3 reaches the cap → quarantined so the offset can move past it.
    guard.mark_attempt([poison])
    assert guard.is_quarantined(poison), "must quarantine after max_attempts (no hot-loop)"


def test_success_clears_attempts(isolated_db):
    """Clearing a processed id resets its counter so a later reuse isn't pre-poisoned."""
    guard = PoisonGuard("telegram", max_attempts=3)
    guard.mark_attempt(["m1"])
    guard.mark_attempt(["m1"])
    guard.clear(["m1"])
    guard.mark_attempt(["m1"])
    assert not guard.is_quarantined("m1"), "counter must reset after a successful clear"


def test_healthy_id_never_quarantined(isolated_db):
    guard = PoisonGuard("telegram", max_attempts=3)
    guard.mark_attempt(["ok"])
    assert not guard.is_quarantined("ok")


def test_partition_helper(isolated_db):
    """partition() marks attempts and splits ids into (live, poison)."""
    guard = PoisonGuard("whatsapp", max_attempts=2)
    # First pass: both live.
    live, poison = guard.partition(["a", "b"])
    assert set(live) == {"a", "b"} and poison == []
    # Second pass without clearing: both reach cap → both poison.
    live, poison = guard.partition(["a", "b"])
    assert live == [] and set(poison) == {"a", "b"}
