"""SPEC-013 A22–A24 — mentions only reach people who can see the conversation (app role)."""

from __future__ import annotations

import uuid

from sqlalchemy import text

from mihomes.models.chat_native import ChatConversation
from mihomes.models.membership import Membership
from mihomes.services.chat import conversations as conv
from mihomes.services.chat import mentions as mention_svc
from mihomes.services.chat.audience import display_name
from tests.integration.chat_helpers import app_client, app_session, estate

__all__ = ["app_client", "estate"]  # re-exported pytest fixtures (see chat_helpers)


def _names(app_engine, e) -> dict[str, str]:
    with app_session(app_engine, e.account_id) as s:
        return {k: display_name(s, s.get(Membership, getattr(e, k)))
                for k in ("owner", "admin", "staff_belle", "staff_blue", "revoked")}


def _rooms(app_engine, e):
    with app_session(app_engine, e.account_id) as s:
        ids = {"belle": conv.get_or_create_property(s, e.belle).id,
               "general": conv.get_or_create_estate(s).id}
        s.commit()
        return ids


def _mentioned(_pg_engine, post_html) -> set[uuid.UUID]:
    import re

    post_id = re.search(r'id="post-([0-9a-f-]{36})"', post_html).group(1)
    with _pg_engine.connect() as c:
        return set(c.execute(text("SELECT membership_id FROM chat_mentions WHERE post_id = :p"),
                             {"p": post_id}).scalars())


def test_non_visible_member_not_mentioned(app_client, app_engine, _pg_engine, estate, monkeypatch):
    """A22 — by name and by a forged mention_ids value, a Blue-only member is never mentioned in
    Belle's chat; Belle staff are."""
    from mihomes.services.chat import ai_bridge

    monkeypatch.setattr(ai_bridge, "dispatch", lambda *a, **k: None)
    e = estate
    rooms = _rooms(app_engine, e)
    n = _names(app_engine, e)
    owner = app_client(e.account_id, e.owner)
    r = owner.post(f"/chat/c/{rooms['belle']}/posts",
                   data={"body": f"@{n['staff_blue']} and @{n['staff_belle']} and @{n['revoked']}",
                         "mention_ids": [str(e.staff_blue), str(e.revoked)]})
    assert r.status_code == 200
    assert _mentioned(_pg_engine, r.text) == {e.staff_belle}

    # The owner mentioning themself is not a mention.
    r = owner.post(f"/chat/c/{rooms['belle']}/posts", data={"body": f"@{n['owner']} note to self"})
    assert _mentioned(_pg_engine, r.text) == set()


def test_mentionable_scoped(app_client, app_engine, estate):
    """A23 — the picker lists only people who can see the room; a non-member gets 404."""
    e = estate
    rooms = _rooms(app_engine, e)
    n = _names(app_engine, e)
    picker = app_client(e.account_id, e.owner).get(f"/chat/c/{rooms['belle']}/mentionable?q=")
    assert picker.status_code == 200
    assert n["staff_belle"] in picker.text and n["admin"] in picker.text
    assert n["staff_blue"] not in picker.text and n["revoked"] not in picker.text
    assert n["owner"] not in picker.text   # not yourself
    blue = app_client(e.account_id, e.staff_blue).get(f"/chat/c/{rooms['belle']}/mentionable?q=")
    assert blue.status_code == 404


def test_ambiguous_name_needs_hint(app_engine, _pg_engine, estate):
    """A24 — two visible members with the same name: no hint → neither; a hint → only that one."""
    e = estate
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE users SET name = 'Maria' WHERE id IN (SELECT user_id FROM "
                       "memberships WHERE id = ANY(:m))"), {"m": [e.admin, e.staff_belle]})
    rooms = _rooms(app_engine, e)
    with app_session(app_engine, e.account_id) as s:
        room = s.get(ChatConversation, rooms["general"])
        assert mention_svc.resolve_mentions(s, room, "hey @Maria", []) == []
        hinted = mention_svc.resolve_mentions(s, room, "hey @Maria", [e.staff_belle])
        assert [m.id for m in hinted] == [e.staff_belle]
        # A hint without the @name in the text mentions nobody.
        assert mention_svc.resolve_mentions(s, room, "hey there", [e.staff_belle]) == []
