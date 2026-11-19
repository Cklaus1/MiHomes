"""SPEC-012 A6–A9 — who sees which conversation, proven on the non-superuser app role.

Every assertion here is about `services.chat.access`, the one function that decides chat visibility.
Run as `postgres` these would pass even with RLS broken, so they all go through `app_session`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from mihomes.models.membership import Membership
from mihomes.services.chat import conversations as conv
from mihomes.services.chat.access import (
    ChatNotFound,
    require_visible,
    visible_conversation_ids,
)
from tests.integration.chat_helpers import app_session, cleanup_estate, estate, seed_estate

__all__ = ["estate"]  # re-exported pytest fixture (see chat_helpers)


def _setup_conversations(app_engine, e):
    """General, both property chats, and three DMs, created through the service and committed."""
    with app_session(app_engine, e.account_id) as s:
        owner = s.get(Membership, e.owner)
        general = conv.get_or_create_estate(s)
        belle = conv.get_or_create_property(s, e.belle)
        blue = conv.get_or_create_property(s, e.blue)
        owner_belle_dm = conv.get_or_create_dm(s, owner, e.staff_belle)
        staff_dm = conv.get_or_create_dm(s, s.get(Membership, e.staff_belle), e.staff_blue)
        s.commit()
        return {"general": general.id, "belle": belle.id, "blue": blue.id,
                "owner_belle_dm": owner_belle_dm.id, "staff_dm": staff_dm.id}


def _visible(app_engine, e, membership_id):
    with app_session(app_engine, e.account_id) as s:
        return visible_conversation_ids(s, s.get(Membership, membership_id))


def test_staff_visible_set(app_engine, estate):
    """A6 — General + their property + their own DMs; never another property, even with a
    leftover participant row (group read cursors also live in chat_participants)."""
    e = estate
    c = _setup_conversations(app_engine, e)

    # A read cursor in Blue's chat — left over from when they had Blue, say. It must not grant Blue.
    with app_session(app_engine, e.account_id) as s:
        s.execute(text("INSERT INTO chat_participants (account_id, conversation_id, membership_id) "
                       "VALUES (:a, :c, :m)"),
                  {"a": e.account_id, "c": c["blue"], "m": e.staff_belle})
        s.commit()

    assert _visible(app_engine, e, e.staff_belle) == {
        c["general"], c["belle"], c["owner_belle_dm"], c["staff_dm"]}


def test_owner_sees_every_property_but_not_others_dms(app_engine, estate):
    """A7 (visible-set half) — owners see all group chats and their own DMs, nobody else's."""
    e = estate
    c = _setup_conversations(app_engine, e)
    assert _visible(app_engine, e, e.owner) == {
        c["general"], c["belle"], c["blue"], c["owner_belle_dm"]}
    assert c["staff_dm"] not in _visible(app_engine, e, e.admin)


def test_owner_cannot_read_others_dm(app_engine, estate):
    """A7 — require_visible refuses (404, not 403) a staff-to-staff DM for the owner."""
    e = estate
    c = _setup_conversations(app_engine, e)
    with app_session(app_engine, e.account_id) as s:
        with pytest.raises(ChatNotFound):
            require_visible(s, s.get(Membership, e.owner), c["staff_dm"])
        assert require_visible(s, s.get(Membership, e.staff_blue), c["staff_dm"]).id == c["staff_dm"]


def test_unscoped_staff_see_only_general(app_engine, estate):
    e = estate
    c = _setup_conversations(app_engine, e)
    assert _visible(app_engine, e, e.staff_none) == {c["general"]}


def test_revoked_sees_nothing(app_engine, estate):
    """A8 — even with a property scope row and a participant row left behind."""
    e = estate
    c = _setup_conversations(app_engine, e)
    with app_session(app_engine, e.account_id) as s:
        s.execute(text("INSERT INTO chat_participants (account_id, conversation_id, membership_id) "
                       "VALUES (:a, :c, :m)"),
                  {"a": e.account_id, "c": c["belle"], "m": e.revoked})
        s.commit()
    assert _visible(app_engine, e, e.revoked) == set()


def test_cross_account_invisible(_pg_engine, app_engine, estate, account_b):
    """A9 — account B's conversations are absent from A's set and refused by require_visible."""
    e = estate
    a_ids = _setup_conversations(app_engine, e)
    other = seed_estate(_pg_engine, account_b)
    try:
        b_ids = _setup_conversations(app_engine, other)
        assert not (set(b_ids.values()) & _visible(app_engine, e, e.owner))
        with app_session(app_engine, e.account_id) as s:
            with pytest.raises(ChatNotFound):
                require_visible(s, s.get(Membership, e.owner), b_ids["general"])
        assert a_ids["general"] != b_ids["general"]
    finally:
        cleanup_estate(_pg_engine, other)


def test_group_creation_is_idempotent(app_engine, estate):
    """D6 — ensure_group_conversations twice: one General, one chat per visible property."""
    e = estate
    for _ in range(2):
        with app_session(app_engine, e.account_id) as s:
            conv.ensure_group_conversations(s, s.get(Membership, e.owner))
            s.commit()
    with app_session(app_engine, e.account_id) as s:
        kinds = s.execute(text("SELECT kind, count(*) FROM chat_conversations "
                               "WHERE account_id = :a GROUP BY kind"),
                          {"a": e.account_id}).all()
    assert dict(kinds) == {"estate": 1, "property": 2}


def test_dm_rules(app_engine, estate):
    """A22 (service half) — no DM with yourself or a revoked member; either side reaches the same
    conversation."""
    from mihomes.services.chat.access import ChatInvalid

    e = estate
    with app_session(app_engine, e.account_id) as s:
        owner = s.get(Membership, e.owner)
        with pytest.raises(ChatInvalid):
            conv.get_or_create_dm(s, owner, e.owner)
        with pytest.raises(ChatInvalid):
            conv.get_or_create_dm(s, owner, e.revoked)
        with pytest.raises(ChatInvalid):
            conv.get_or_create_dm(s, owner, uuid.uuid4())
        one = conv.get_or_create_dm(s, owner, e.staff_belle).id
        two = conv.get_or_create_dm(s, s.get(Membership, e.staff_belle), e.owner).id
        s.commit()
    assert one == two


def test_list_titles_come_from_the_visible_set(app_engine, estate):
    """A staff member's list never names a property they cannot see."""
    e = estate
    _setup_conversations(app_engine, e)
    with app_session(app_engine, e.account_id) as s:
        rows = conv.list_for(s, s.get(Membership, e.staff_belle))
    titles = [r.title for r in rows]
    assert titles[0] == "General"
    assert "Belle" in titles and "Blue" not in titles
    assert [r.kind for r in rows][:2] == ["estate", "property"]
