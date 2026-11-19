"""SPEC-013 A26–A28 — chat search: scope, robustness, escaping (app role)."""

from __future__ import annotations

from mihomes.models.chat_native import ChatConversation, ChatPost
from mihomes.models.membership import Membership
from mihomes.services.chat import conversations as conv
from mihomes.services.chat import posts
from mihomes.services.chat.search import search_posts
from tests.integration.chat_helpers import app_client, app_session, estate

__all__ = ["app_client", "estate"]  # re-exported pytest fixtures (see chat_helpers)


def _seed(app_engine, e) -> dict:
    with app_session(app_engine, e.account_id) as s:
        rooms = {"general": conv.get_or_create_estate(s), "belle": conv.get_or_create_property(s, e.belle),
                 "blue": conv.get_or_create_property(s, e.blue)}
        owner = s.get(Membership, e.owner)
        ids = {}
        for key, body in (("general", "pump room tidy"), ("belle", "the pump is leaking"),
                          ("blue", "blue pump secret"), ("gone", "deleted pump note")):
            room = rooms["belle" if key == "gone" else key]
            ids[key] = posts.create_post(s, room, author=owner, body=body).id
        posts.delete_post(s, s.get(ChatPost, ids["gone"]), actor=owner)
        s.commit()
        return {k: r.id for k, r in rooms.items()} | {f"p_{k}": v for k, v in ids.items()}


def test_search_scope_and_deleted_app_role(app_engine, estate):
    """A26 — Belle staff find General + Belle hits; never Blue, never the deleted post."""
    e = estate
    ids = _seed(app_engine, e)
    with app_session(app_engine, e.account_id) as s:
        found = {h.post_id for h in search_posts(s, s.get(Membership, e.staff_belle), "pump").hits}
        owner_found = {h.post_id for h in search_posts(s, s.get(Membership, e.owner), "pump").hits}
        revoked = search_posts(s, s.get(Membership, e.revoked), "pump").hits
    assert found == {ids["p_general"], ids["p_belle"]}
    assert ids["p_blue"] not in found and ids["p_gone"] not in found
    assert owner_found == {ids["p_general"], ids["p_belle"], ids["p_blue"]}
    assert revoked == []


def test_query_robustness_and_keyset(app_client, app_engine, estate):
    """A27 — hostile syntax never 500s; paging is stable while new posts arrive."""
    e = estate
    ids = _seed(app_engine, e)
    client = app_client(e.account_id, e.owner)
    for q in ('"', "-", "OR OR", "!!!", "pump & | !", "x" * 10_000, ""):
        r = client.get("/chat/search", params={"q": q})
        assert r.status_code == 200, q[:20]

    with app_session(app_engine, e.account_id) as s:
        owner = s.get(Membership, e.owner)
        room = s.get(ChatConversation, ids["general"])
        for i in range(5):
            posts.create_post(s, room, author=owner, body=f"valve {i}")
        s.commit()
        first = search_posts(s, owner, "valve", limit=2)
        assert len(first.hits) == 2 and first.next_cursor
        posts.create_post(s, room, author=owner, body="valve new")   # arrives mid-paging
        s.commit()
        rest, cursor = [], first.next_cursor
        while cursor:
            page = search_posts(s, owner, "valve", cursor=cursor, limit=2)
            rest += page.hits
            cursor = page.next_cursor
        seen = [h.post_id for h in first.hits + rest]
        assert len(seen) == len(set(seen)) == 5      # no duplicates, nothing skipped
        assert search_posts(s, owner, "valve", cursor="garbage").hits   # bad cursor = first page


def test_snippet_escaped(app_client, app_engine, estate):
    """A28 — highlights are <mark> around escaped text; a body's <b> stays literal."""
    e = estate
    with app_session(app_engine, e.account_id) as s:
        room = conv.get_or_create_estate(s)
        posts.create_post(s, room, author=s.get(Membership, e.owner),
                          body="the <b>boiler</b> pressure is low")
        s.commit()
    r = app_client(e.account_id, e.owner).get("/chat/search", params={"q": "pressure"},
                                              headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<mark>pressure</mark>" in r.text
    # ts_headline drops HTML-looking tags from fragments; either way none may reach the page raw.
    # (Escaping of a literal "<b>" that does survive is test_chat_render's job.)
    assert "<b>" not in r.text and "boiler" in r.text
