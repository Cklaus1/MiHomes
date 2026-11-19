"""SPEC-012 A4, A5 — the chat schema refuses impossible rows on its own.

Raw SQL against the real schema (superuser connection, one transaction rolled back at the end), so
these assert the CHECKs and partial unique indexes themselves, not the service code above them.
Each refusal runs in a SAVEPOINT so one failed insert does not abort the rest.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def conn(_pg_engine, account_a, account_b):
    c = _pg_engine.connect()
    tx = c.begin()
    try:
        yield c
    finally:
        tx.rollback()
        c.close()


def _membership(conn, account_id) -> uuid.UUID:
    uid, mid = uuid.uuid4(), uuid.uuid4()
    conn.execute(text("INSERT INTO users (id, google_sub, email) VALUES (:u, :s, :e)"),
                 {"u": uid, "s": f"sub-{uid.hex[:10]}", "e": f"chat-{uid.hex[:8]}@example.com"})
    conn.execute(text("INSERT INTO memberships (id, account_id, user_id, role, status) "
                      "VALUES (:m, :a, :u, 'staff', 'active')"), {"m": mid, "a": account_id, "u": uid})
    return mid


def _property(conn, account_id) -> uuid.UUID:
    pid = uuid.uuid4()
    conn.execute(text("INSERT INTO properties (id, account_id, name, slug, property_type, status, "
                      "currency, occupied) VALUES (:p, :a, :n, :s, 'OTHER', 'OPEN', 'USD', false)"),
                 {"p": pid, "a": account_id, "n": "Belle", "s": f"belle-{pid.hex[:6]}"})
    return pid


def _conversation(conn, account_id, *, kind="estate", property_id=None, dm_key=None) -> uuid.UUID:
    cid = uuid.uuid4()
    conn.execute(text("INSERT INTO chat_conversations (id, account_id, kind, property_id, dm_key) "
                      "VALUES (:c, :a, :k, :p, :d)"),
                 {"c": cid, "a": account_id, "k": kind, "p": property_id, "d": dm_key})
    return cid


def _post(conn, account_id, conversation_id, *, seq=1, author=None, kind="member", body="hi",
          deleted=False):
    conn.execute(
        text("INSERT INTO chat_posts (id, account_id, conversation_id, seq, author_membership_id, "
             "author_kind, body, deleted_at) VALUES (:i, :a, :c, :s, :m, :k, :b, "
             "CASE WHEN :d THEN now() END)"),
        {"i": uuid.uuid4(), "a": account_id, "c": conversation_id, "s": seq, "m": author,
         "k": kind, "b": body, "d": deleted})


def _refused(conn, fn, *args, **kwargs) -> bool:
    sp = conn.begin_nested()
    try:
        fn(conn, *args, **kwargs)
    except IntegrityError:
        sp.rollback()
        return True
    sp.rollback()
    return False


def test_checks_refuse_impossible_rows(conn, account_a):
    """A4."""
    member = _membership(conn, account_a)
    estate = _conversation(conn, account_a)

    assert _refused(conn, _conversation, account_a, kind="property"), "property chat with no property"
    assert _refused(conn, _conversation, account_a, kind="dm"), "DM with no dm_key"
    assert _refused(conn, _conversation, account_a, kind="nonsense"), "unknown kind"
    assert _refused(conn, _post, account_a, estate, author=member, kind="bot"), "bot post with an author"
    assert _refused(conn, _post, account_a, estate, author=None, kind="member"), "member post, no author"
    assert _refused(conn, _post, account_a, estate, author=member, body="x" * 4001), "member > 4000"
    assert _refused(conn, _post, account_a, estate, author=member, deleted=True), "deleted with text"

    # The paired positives — each CHECK still admits the legitimate row.
    assert not _refused(conn, _post, account_a, estate, seq=1, author=member, body="x" * 4000)
    assert not _refused(conn, _post, account_a, estate, seq=2, kind="bot", body="y" * 9000)
    assert not _refused(conn, _post, account_a, estate, seq=3, author=member, body="", deleted=True)


def test_partial_uniques_are_per_account(conn, account_a, account_b):
    """A5 — one estate chat, one per property, one per dm_key: within an account, not across."""
    prop = _property(conn, account_a)
    _conversation(conn, account_a)
    _conversation(conn, account_a, kind="property", property_id=prop)
    _conversation(conn, account_a, kind="dm", dm_key="a:b")

    assert _refused(conn, _conversation, account_a), "second estate chat"
    assert _refused(conn, _conversation, account_a, kind="property", property_id=prop)
    assert _refused(conn, _conversation, account_a, kind="dm", dm_key="a:b")

    assert not _refused(conn, _conversation, account_b), "another account has its own estate chat"
    assert not _refused(conn, _conversation, account_b, kind="dm", dm_key="a:b")


def test_seq_is_unique_per_conversation(conn, account_a):
    member = _membership(conn, account_a)
    estate = _conversation(conn, account_a)
    _post(conn, account_a, estate, seq=1, author=member)
    assert _refused(conn, _post, account_a, estate, seq=1, author=member)
