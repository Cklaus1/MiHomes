"""SPEC-014 A4–A12, A15 — the notification rule and its markers, on the app role.

`now` is injected (no sleeps); posts are backdated. Each negative has a positive control in the
same test: a member who IS notified, so "nobody was notified" cannot pass by reading nothing.
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta

from sqlalchemy import text

from mihomes.models.membership import Membership
from mihomes.services.chat import posts
from tests.integration.chat_helpers import app_session, estate
from tests.integration.chat_notify_helpers import (
    NOW,
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


def test_unread_dm_notified(app_engine, _pg_engine, estate):
    """A4 — one push and one digest entry, for the recipient only."""
    e = estate
    r = rooms(app_engine, e)
    staff_ep = subscribe(app_engine, e, e.staff_belle)
    owner_ep = subscribe(app_engine, e, e.owner)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "SENTINEL-dm-body", age=OLD)
    sender = FakePushSender()
    run(app_engine, sender=sender)
    assert [ep for ep, _p in sender.sent] == [staff_ep]
    assert owner_ep not in [ep for ep, _p in sender.sent]
    payload = sender.sent[0][1]
    assert payload["title"] == "Direct message" and payload["body"] == "New message"
    assert "SENTINEL" not in json.dumps(payload)
    mails = digests(_pg_engine, e)
    assert [to for to, _c in mails] == [email_of(_pg_engine, e.staff_belle)]
    ctx = json.loads(mails[0][1])
    assert ctx["conversations"] == [{"title": "Direct messages", "count": 1}]


def test_window_10min_to_48h(app_engine, _pg_engine, estate):
    """A5 — younger than 10 min: not yet; older than 48 h: never. The edges at ±1 s."""
    e = estate
    r = rooms(app_engine, e)
    subscribe(app_engine, e, e.staff_belle)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "too new",
         age=timedelta(minutes=10) - timedelta(seconds=1))
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "too old",
         age=timedelta(hours=48) + timedelta(seconds=1))
    sender = FakePushSender()
    run(app_engine, sender=sender)
    assert sender.sent == [] and digests(_pg_engine, e) == []
    # Positive control: exactly ten minutes old is due.
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "just due", age=timedelta(minutes=10))
    run(app_engine, sender=sender)
    assert len(sender.sent) == 1


def test_read_post_not_notified(app_engine, _pg_engine, estate):
    """A6 — reading advances last_read_seq; a read post is never notified."""
    from mihomes.services.chat import reads

    e = estate
    r = rooms(app_engine, e)
    subscribe(app_engine, e, e.staff_belle)
    pid = post(app_engine, _pg_engine, e, r["dm"], e.owner, "read me", age=OLD)
    with app_session(app_engine, e.account_id) as s:
        from mihomes.models.chat_native import ChatPost

        reads.mark_read(s, s.get(Membership, e.staff_belle), r["dm"], s.get(ChatPost, pid).seq)
        s.commit()
    sender = FakePushSender()
    run(app_engine, sender=sender)
    assert sender.sent == []
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "unread", age=OLD)   # control
    run(app_engine, sender=sender)
    assert len(sender.sent) == 1


def test_own_post_not_notified(app_engine, _pg_engine, estate):
    """A7 — the author is never notified about their own post; the other side is."""
    e = estate
    r = rooms(app_engine, e)
    owner_ep = subscribe(app_engine, e, e.owner)
    staff_ep = subscribe(app_engine, e, e.staff_belle)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "mine", age=OLD)
    sender = FakePushSender()
    run(app_engine, sender=sender)
    assert [ep for ep, _ in sender.sent] == [staff_ep] and owner_ep != staff_ep


def test_muted_not_notified(app_engine, _pg_engine, estate):
    """A8 — conversation mute and muted_until both silence; a mention in a muted room too."""
    e = estate
    r = rooms(app_engine, e)
    subscribe(app_engine, e, e.staff_belle)
    admin_ep = subscribe(app_engine, e, e.admin)
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE chat_participants SET muted = true WHERE conversation_id = :c "
                       "AND membership_id = :m"), {"c": r["dm"], "m": e.staff_belle})
        c.execute(text("INSERT INTO chat_notify_prefs (id, account_id, membership_id, muted_until) "
                       "VALUES (gen_random_uuid(), :a, :m, :u)"),
                  {"a": e.account_id, "m": e.staff_blue, "u": NOW + timedelta(hours=1)})
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "muted dm", age=OLD)
    g = post(app_engine, _pg_engine, e, r["general"], e.owner, "@all", age=OLD)
    mention(_pg_engine, e, g, e.staff_blue)          # muted_until in the future
    mention(_pg_engine, e, g, e.admin)               # control
    sender = FakePushSender()
    run(app_engine, sender=sender)
    assert [ep for ep, _ in sender.sent] == [admin_ep]
    assert sender.sent[0][1]["body"] == "You were mentioned"


def test_deleted_post_not_notified(app_engine, _pg_engine, estate):
    """A9"""
    from mihomes.models.chat_native import ChatPost

    e = estate
    r = rooms(app_engine, e)
    subscribe(app_engine, e, e.staff_belle)
    pid = post(app_engine, _pg_engine, e, r["dm"], e.owner, "oops", age=OLD)
    with app_session(app_engine, e.account_id) as s:
        posts.delete_post(s, s.get(ChatPost, pid), actor=s.get(Membership, e.owner))
        s.commit()
    sender = FakePushSender()
    run(app_engine, sender=sender)
    assert sender.sent == []
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "still here", age=OLD)   # control
    run(app_engine, sender=sender)
    assert len(sender.sent) == 1


def test_notified_once_only(app_engine, _pg_engine, estate):
    """A10 — a second run pushes nothing; a second digest needs a new item AND the interval."""
    e = estate
    r = rooms(app_engine, e)
    subscribe(app_engine, e, e.staff_belle)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "one", age=OLD)
    sender = FakePushSender()
    run(app_engine, sender=sender)
    run(app_engine, sender=sender, now=NOW + timedelta(minutes=5))
    assert len(sender.sent) == 1 and len(digests(_pg_engine, e)) == 1

    later = NOW + timedelta(minutes=30)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "two", age=OLD, now=later)
    run(app_engine, sender=sender, now=later)
    assert len(sender.sent) == 2                      # push: per item
    assert len(digests(_pg_engine, e)) == 1           # digest: interval not yet passed
    run(app_engine, sender=sender, now=NOW + timedelta(minutes=75))
    assert len(digests(_pg_engine, e)) == 2           # new item + interval → second digest
    assert len(sender.sent) == 2


def test_digest_through_is_a_cutoff_not_a_send_time(app_engine, _pg_engine, estate):
    """A11 — an item that turns 10 min old just after a digest lands in the NEXT digest."""
    e = estate
    r = rooms(app_engine, e)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "first", age=OLD)
    # Written 9 minutes before the run: not due yet, so not in the first digest.
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "second", age=timedelta(minutes=9))
    run(app_engine)
    first = json.loads(digests(_pg_engine, e)[0][1])
    assert first["total"] == 1
    run(app_engine, now=NOW + timedelta(minutes=61))
    mails = digests(_pg_engine, e)
    assert len(mails) == 2 and json.loads(mails[1][1])["total"] == 1


def test_concurrent_runs_do_not_double_notify(app_engine, _pg_engine, estate):
    """A12 — two runs at once on one account: each push sent once (advisory lock + markers)."""
    e = estate
    r = rooms(app_engine, e)
    subscribe(app_engine, e, e.staff_belle)
    for i in range(3):
        post(app_engine, _pg_engine, e, r["dm"], e.owner, f"m{i}", age=OLD)
    sender = FakePushSender()
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        run(app_engine, sender=sender)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sender.sent) == 1                      # one payload per conversation, once
    assert len(digests(_pg_engine, e)) == 1


def test_prunes_events_older_than_7_days(app_engine, _pg_engine, estate):
    """A15"""
    e = estate
    r = rooms(app_engine, e)
    post(app_engine, _pg_engine, e, r["general"], e.owner, "old event", age=timedelta(days=9))
    post(app_engine, _pg_engine, e, r["general"], e.owner, "new event", age=timedelta(days=1))
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE chat_events ev SET created_at = p.created_at FROM chat_posts p "
                       "WHERE ev.post_id = p.id AND p.account_id = :a"), {"a": e.account_id})
    run(app_engine)
    with _pg_engine.connect() as c:
        bodies = set(c.execute(text(
            "SELECT p.body FROM chat_events ev JOIN chat_posts p ON p.id = ev.post_id "
            "WHERE ev.account_id = :a"), {"a": e.account_id}).scalars())
    assert bodies == {"new event"}
