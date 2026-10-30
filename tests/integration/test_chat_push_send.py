"""SPEC-014 A20, A23 — what a push response does to the subscription; push off without VAPID."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from tests.integration.chat_helpers import estate
from tests.integration.chat_notify_helpers import (
    FakePushSender,
    digests,
    post,
    rooms,
    run,
    subscribe,
)

__all__ = ["estate"]  # re-exported pytest fixture (see chat_helpers)

OLD = timedelta(minutes=15)


def _subs(_pg_engine, e) -> int:
    with _pg_engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM push_subscriptions WHERE account_id = :a"),
                         {"a": e.account_id}).scalar()


def test_gone_subscription_deleted(app_engine, _pg_engine, estate):
    """A20 — 410 and 404 delete the row; 429 and 500 keep it."""
    e = estate
    r = rooms(app_engine, e)
    for status, kept in ((410, 0), (404, 0), (429, 1), (500, 1)):
        with _pg_engine.begin() as c:
            c.execute(text("DELETE FROM push_subscriptions WHERE account_id = :a"),
                      {"a": e.account_id})
        subscribe(app_engine, e, e.staff_belle)
        post(app_engine, _pg_engine, e, r["dm"], e.owner, f"status {status}", age=OLD)
        sender = FakePushSender(status=status)
        run(app_engine, sender=sender)
        assert len(sender.sent) == 1, status
        assert _subs(_pg_engine, e) == kept, status


def test_no_vapid_disables_push_only(app_engine, _pg_engine, estate, monkeypatch):
    """A23 — no VAPID: the job runs, digests still queue, nothing is pushed, nothing raises."""
    from mihomes.services.chat import push

    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("VAPID_SUBJECT", raising=False)
    assert push.sender_from_env() is None
    e = estate
    r = rooms(app_engine, e)
    subscribe(app_engine, e, e.staff_belle)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "hello", age=OLD)
    result = run(app_engine, sender=push.sender_from_env())
    assert result.errors == [] and result.pushes_sent == 0
    assert len(digests(_pg_engine, e)) == 1
    assert _subs(_pg_engine, e) == 1
