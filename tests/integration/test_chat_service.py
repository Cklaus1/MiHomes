"""SPEC-012 A10–A15 — posting, editing, deleting, reactions and unread, on the app role."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import select, text

from mihomes.models.chat_native import ChatConversation, ChatPost, ChatReaction
from mihomes.models.membership import Membership
from mihomes.services.chat import conversations as conv
from mihomes.services.chat import posts, reactions, reads
from mihomes.services.chat.access import ChatForbidden, ChatInvalid
from tests.integration.chat_helpers import app_session, estate

__all__ = ["estate"]  # re-exported pytest fixture (see chat_helpers)


def _m(s, membership_id) -> Membership:
    return s.get(Membership, membership_id)


def _conversations(app_engine, e) -> dict[str, uuid.UUID]:
    with app_session(app_engine, e.account_id) as s:
        general = conv.get_or_create_estate(s)
        belle = conv.get_or_create_property(s, e.belle)
        staff_dm = conv.get_or_create_dm(s, _m(s, e.staff_belle), e.staff_blue)
        s.commit()
        return {"general": general.id, "belle": belle.id, "staff_dm": staff_dm.id}


def _post(app_engine, e, conversation_id, author_id, body="hello") -> uuid.UUID:
    with app_session(app_engine, e.account_id) as s:
        post = posts.create_post(s, s.get(ChatConversation, conversation_id),
                                 author=_m(s, author_id) if author_id else None, body=body)
        s.commit()
        return post.id


def test_seq_is_gap_free_under_concurrency(app_engine, estate):
    """A10 — two real connections race; the row lock serialises seq."""
    e = estate
    c = _conversations(app_engine, e)
    per_thread, workers = 10, 2
    barrier = threading.Barrier(workers)
    errors: list[BaseException] = []

    def worker(author_id):
        try:
            barrier.wait()
            for i in range(per_thread):
                _post(app_engine, e, c["general"], author_id, f"msg {i}")
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(m,)) for m in (e.owner, e.staff_belle)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    with app_session(app_engine, e.account_id) as s:
        seqs = s.execute(select(ChatPost.seq).where(ChatPost.conversation_id == c["general"])
                         .order_by(ChatPost.seq)).scalars().all()
        next_seq = s.get(ChatConversation, c["general"]).next_seq
    assert seqs == list(range(1, per_thread * workers + 1))
    assert next_seq == per_thread * workers + 1


def test_reply_must_share_conversation(app_engine, estate):
    """A11"""
    e = estate
    c = _conversations(app_engine, e)
    elsewhere = _post(app_engine, e, c["belle"], e.owner)
    here = _post(app_engine, e, c["general"], e.owner)
    with app_session(app_engine, e.account_id) as s:
        general = s.get(ChatConversation, c["general"])
        with pytest.raises(ChatInvalid):
            posts.create_post(s, general, author=_m(s, e.owner), body="re", reply_to_id=elsewhere)
        reply = posts.create_post(s, general, author=_m(s, e.owner), body="re", reply_to_id=here)
        assert reply.reply_to_id == here


def test_create_post_rules(app_engine, estate):
    """Empty bodies, client_token dedup, visibility and the bot's DM ban."""
    e = estate
    c = _conversations(app_engine, e)
    with app_session(app_engine, e.account_id) as s:
        general = s.get(ChatConversation, c["general"])
        owner = _m(s, e.owner)
        with pytest.raises(ChatInvalid):
            posts.create_post(s, general, author=owner, body="   ")
        assert posts.create_post(s, general, author=owner, body="", allow_empty=True).body == ""
        token = uuid.uuid4()
        first = posts.create_post(s, general, author=owner, body="once", client_token=token)
        again = posts.create_post(s, general, author=owner, body="once", client_token=token)
        assert first.id == again.id
        with pytest.raises(ChatInvalid):
            posts.create_post(s, general, author=owner, body="x" * (posts.MAX_MEMBER_BODY + 1))
        # The owner cannot see the staff-to-staff DM, so cannot post in it either.
        with pytest.raises(ChatInvalid):
            posts.create_post(s, s.get(ChatConversation, c["staff_dm"]), author=owner, body="hi")
        with pytest.raises(ChatInvalid):
            posts.create_bot_post(s, c["staff_dm"], "hi")
        bot = posts.create_bot_post(s, c["general"], "Logged it.")
        assert bot.author_kind == "bot" and bot.author_membership_id is None
        s.commit()


def test_edit_rules(app_engine, estate):
    """A12"""
    e = estate
    c = _conversations(app_engine, e)
    mine = _post(app_engine, e, c["general"], e.staff_belle)
    bot = _post(app_engine, e, c["general"], None)
    with app_session(app_engine, e.account_id) as s:
        author, other = _m(s, e.staff_belle), _m(s, e.owner)
        post = s.get(ChatPost, mine)
        with pytest.raises(ChatForbidden):
            posts.edit_post(s, post, actor=other, body="hijack")
        with pytest.raises(ChatForbidden):
            posts.edit_post(s, s.get(ChatPost, bot), actor=other, body="nope")
        edited = posts.edit_post(s, post, actor=author, body="fixed")
        assert edited.body == "fixed" and edited.edited_at is not None
        posts.delete_post(s, post, actor=author)
        with pytest.raises(ChatInvalid):
            posts.edit_post(s, post, actor=author, body="back")
        s.commit()


def test_delete_rules(app_engine, estate):
    """A13"""
    e = estate
    c = _conversations(app_engine, e)
    in_group = _post(app_engine, e, c["general"], e.staff_belle)
    by_owner = _post(app_engine, e, c["general"], e.owner)
    in_dm = _post(app_engine, e, c["staff_dm"], e.staff_belle)
    with app_session(app_engine, e.account_id) as s:
        reactions.toggle_reaction(s, s.get(ChatPost, in_group), actor=_m(s, e.owner), emoji="👍")
        s.commit()

    with app_session(app_engine, e.account_id) as s:
        with pytest.raises(ChatForbidden):
            posts.delete_post(s, s.get(ChatPost, by_owner), actor=_m(s, e.staff_belle))
        with pytest.raises(ChatForbidden):
            posts.delete_post(s, s.get(ChatPost, in_dm), actor=_m(s, e.admin))
        deleted = posts.delete_post(s, s.get(ChatPost, in_group), actor=_m(s, e.admin))
        assert deleted.body == "" and deleted.deleted_at is not None
        when = deleted.deleted_at
        assert posts.delete_post(s, deleted, actor=_m(s, e.admin)).deleted_at == when  # idempotent
        assert posts.delete_post(s, s.get(ChatPost, in_dm),
                                 actor=_m(s, e.staff_belle)).deleted_at is not None
        s.commit()
        assert not s.execute(select(ChatReaction).where(ChatReaction.post_id == in_group)).all()


def test_reaction_toggle_and_allowlist(app_engine, estate):
    """A14"""
    e = estate
    c = _conversations(app_engine, e)
    pid = _post(app_engine, e, c["general"], e.owner)
    with app_session(app_engine, e.account_id) as s:
        post, actor = s.get(ChatPost, pid), _m(s, e.staff_belle)
        assert reactions.toggle_reaction(s, post, actor=actor, emoji="✅") is True
        assert reactions.toggle_reaction(s, post, actor=actor, emoji="👀") is True
        assert reactions.toggle_reaction(s, post, actor=actor, emoji="✅") is False
        with pytest.raises(ChatInvalid):
            reactions.toggle_reaction(s, post, actor=actor, emoji="🍕")
        s.commit()
        left = s.execute(select(ChatReaction.emoji).where(ChatReaction.post_id == pid)).scalars()
        assert list(left) == ["👀"]


def test_unread_counts(_pg_engine, app_engine, estate):
    """A15 — own and deleted posts don't count; cursors never go backwards; a new member's group
    unread starts at their join."""
    e = estate
    c = _conversations(app_engine, e)
    _post(app_engine, e, c["general"], e.staff_belle)          # staff's own
    _post(app_engine, e, c["general"], e.owner, "one")
    gone = _post(app_engine, e, c["general"], e.owner, "two")
    third = _post(app_engine, e, c["general"], e.owner, "three")
    with app_session(app_engine, e.account_id) as s:
        posts.delete_post(s, s.get(ChatPost, gone), actor=_m(s, e.owner))
        s.commit()

    with app_session(app_engine, e.account_id) as s:
        staff = _m(s, e.staff_belle)
        # Staff posted first, so their cursor sits at seq 1; "one" and "three" are unread.
        assert reads.unread_counts(s, staff, [c["general"]]) == {c["general"]: 2}
        assert reads.unread_counts(s, _m(s, e.owner), [c["general"]]) == {}
        third_seq = s.get(ChatPost, third).seq
        assert reads.mark_read(s, staff, c["general"], third_seq) is True
        assert reads.mark_read(s, staff, c["general"], 1) is False      # never backwards
        assert reads.last_read_seq(s, staff.id, c["general"]) == third_seq
        assert reads.total_unread(s, staff) == 0
        s.commit()

    # A member who joined after these posts (no participant row) starts at zero.
    with _pg_engine.begin() as conn:
        conn.execute(text("UPDATE memberships SET created_at = now() + interval '1 minute' "
                          "WHERE id = :m"), {"m": e.staff_none})
    with app_session(app_engine, e.account_id) as s:
        assert reads.unread_counts(s, _m(s, e.staff_none), [c["general"]]) == {}
