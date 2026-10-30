"""SPEC-014 A3, A13, A14, A35 — who may be notified, on the **app role** only.

Every negative here has a positive control in the same run: someone who can see the conversation
and IS notified. Without it, a job that read nothing under RLS would pass "X was not notified".
A14 is this stage's definition of done.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from tests.integration.chat_helpers import app_session, cleanup_estate, estate, seed_estate
from tests.integration.chat_notify_helpers import (
    FakePushSender,
    digests,
    email_of,
    mention,
    post,
    rooms,
    run,
    subscribe,
)

__all__ = ["estate"]  # re-exported pytest fixture (see chat_helpers)

OLD = timedelta(minutes=15)


def test_tables_isolated_by_account(app_engine, _pg_engine, estate, account_b):
    """A3 — account B's session sees none of A's subscriptions or prefs; A's sees its own."""
    e = estate
    subscribe(app_engine, e, e.owner)
    with _pg_engine.begin() as c:
        c.execute(text("INSERT INTO chat_notify_prefs (id, account_id, membership_id) "
                       "VALUES (gen_random_uuid(), :a, :m)"), {"a": e.account_id, "m": e.owner})
    other = seed_estate(_pg_engine, account_b)
    try:
        for table in ("push_subscriptions", "chat_notify_prefs"):
            with app_session(app_engine, other.account_id) as s:
                assert s.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608
            with app_session(app_engine, e.account_id) as s:
                assert s.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 1  # noqa: S608
    finally:
        cleanup_estate(_pg_engine, other)


def test_revoked_member_not_notified(app_engine, _pg_engine, estate):
    """A13 — revoked before the post, or after it but before the run: nothing either way."""
    e = estate
    r = rooms(app_engine, e)
    staff_ep = subscribe(app_engine, e, e.staff_belle)
    admin_ep = subscribe(app_engine, e, e.admin)
    g = post(app_engine, _pg_engine, e, r["general"], e.owner, "heads up", age=OLD)
    mention(_pg_engine, e, g, e.staff_belle)
    mention(_pg_engine, e, g, e.admin)                       # control
    mention(_pg_engine, e, g, e.revoked)                     # revoked before the post
    with _pg_engine.begin() as c:                            # revoked after the post
        c.execute(text("UPDATE memberships SET status = 'revoked' WHERE id = :m"),
                  {"m": e.staff_belle})
    sender = FakePushSender()
    run(app_engine, sender=sender)
    assert [ep for ep, _ in sender.sent] == [admin_ep]
    assert staff_ep not in [ep for ep, _ in sender.sent]
    assert [to for to, _ in digests(_pg_engine, e)] == [email_of(_pg_engine, e.admin)]


def test_mention_of_non_visible_member_never_notified(app_engine, _pg_engine, estate):
    """A14 — a mention row for someone who cannot see the room (scope narrowed after it was
    written) is never notified, by push or by digest. Belle staff, mentioned the same way,
    are — the positive control."""
    e = estate
    r = rooms(app_engine, e)
    blue_ep = subscribe(app_engine, e, e.staff_blue)
    belle_ep = subscribe(app_engine, e, e.staff_belle)
    pid = post(app_engine, _pg_engine, e, r["belle"], e.owner, "SENTINEL-belle-secret", age=OLD)
    mention(_pg_engine, e, pid, e.staff_blue)
    mention(_pg_engine, e, pid, e.staff_belle)
    sender = FakePushSender()
    run(app_engine, sender=sender)
    assert [ep for ep, _ in sender.sent] == [belle_ep]
    assert blue_ep not in [ep for ep, _ in sender.sent]
    assert [to for to, _ in digests(_pg_engine, e)] == [email_of(_pg_engine, e.staff_belle)]

    # Scope narrowed later: Belle staff lose Belle, and a new mention of them is not sent either.
    with _pg_engine.begin() as c:
        c.execute(text("DELETE FROM membership_property_scopes WHERE membership_id = :m"),
                  {"m": e.staff_belle})
    pid2 = post(app_engine, _pg_engine, e, r["belle"], e.owner, "after", age=OLD)
    mention(_pg_engine, e, pid2, e.staff_belle)
    sender2 = FakePushSender()
    run(app_engine, sender=sender2)
    assert sender2.sent == []


def test_end_to_end_under_app_role(app_engine, _pg_engine, estate):
    """A35 — run_chat_notify with every session on the app role: a push reaches the fake
    sender, and exactly one digest row sits in email_outbox."""
    e = estate
    r = rooms(app_engine, e)
    ep = subscribe(app_engine, e, e.staff_belle)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "ping", age=OLD)
    sender = FakePushSender()
    result = run(app_engine, sender=sender)
    assert [x for x, _ in sender.sent] == [ep]
    assert result.pushes_sent >= 1 and result.errors == []
    assert len(digests(_pg_engine, e)) == 1
    with _pg_engine.connect() as c:
        last = c.execute(text("SELECT last_success_at FROM push_subscriptions WHERE endpoint = :e"),
                         {"e": ep}).scalar()
    assert last is not None
