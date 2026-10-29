"""SPEC-013 A17 — `members_who_can_see` is exactly the inverse of `visible_conversation_ids`.

Over every seeded membership (owner, admin, staff-Belle, staff-Blue, unscoped staff, revoked) and
every conversation kind, on the app role.
"""

from __future__ import annotations

from sqlalchemy import select

from mihomes.models.chat_native import ChatConversation
from mihomes.models.membership import Membership
from mihomes.services.chat import conversations as conv
from mihomes.services.chat.access import visible_conversation_ids
from mihomes.services.chat.audience import audience, display_name, members_who_can_see
from tests.integration.chat_helpers import app_session, estate

__all__ = ["estate"]  # re-exported pytest fixture (see chat_helpers)


def test_inverse_of_visible_conversation_ids(app_engine, estate):
    e = estate
    with app_session(app_engine, e.account_id) as s:
        owner = s.get(Membership, e.owner)
        conv.get_or_create_estate(s)
        conv.get_or_create_property(s, e.belle)
        conv.get_or_create_property(s, e.blue)
        conv.get_or_create_dm(s, owner, e.staff_belle)
        conv.get_or_create_dm(s, s.get(Membership, e.staff_belle), e.staff_blue)
        s.commit()

        members = s.execute(select(Membership).where(Membership.account_id == e.account_id)).scalars().all()
        conversations = s.execute(select(ChatConversation)
                                  .where(ChatConversation.account_id == e.account_id)).scalars().all()
        assert len(members) == 6 and len(conversations) == 5
        visible = {m.id: visible_conversation_ids(s, m) for m in members}
        checked = 0
        for c in conversations:
            can_see = {m.id for m in members_who_can_see(s, c)}
            for m in members:
                assert (m.id in can_see) == (c.id in visible[m.id]), (m.role, m.id, c.kind)
                checked += 1
        assert checked == 30
        assert e.revoked not in {m.id for c in conversations for m in members_who_can_see(s, c)}


def test_audience_intersection(app_engine, estate):
    """D7 inputs: General with staff split across properties → empty scope (fail closed);
    Belle's chat → {Belle}; an owner-only DM → no staff."""
    e = estate
    with app_session(app_engine, e.account_id) as s:
        general = conv.get_or_create_estate(s)
        belle = conv.get_or_create_property(s, e.belle)
        owner_dm = conv.get_or_create_dm(s, s.get(Membership, e.owner), e.admin)
        s.commit()
        g, b, d = audience(s, general), audience(s, belle), audience(s, owner_dm)
        assert g.has_staff and g.scope == frozenset()
        assert b.has_staff and b.scope == frozenset({e.belle})
        assert not d.has_staff and d.scope is None
        assert display_name(s, s.get(Membership, e.owner)).startswith("Owner")
