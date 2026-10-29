"""SPEC-012 A2, A20–A25, A27 — the chat pages through the real app on the app role.

Every client here comes from `chat_helpers.app_client`: real `get_db`, non-superuser role, RLS live.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import text

from mihomes.authz.declare import ACCESS_ATTR, ACTION_ATTR
from mihomes.models.chat_native import ChatAttachment, ChatConversation, ChatPost
from mihomes.models.membership import Membership
from mihomes.services.chat import conversations as conv
from mihomes.services.chat import posts
from tests.integration.chat_helpers import app_client, app_session, estate

__all__ = ["app_client", "estate"]  # re-exported pytest fixtures (see chat_helpers)

_ID_PARAMS = ("{conversation_id}", "{post_id}", "{attachment_id}")


def _groups(app_engine, e) -> dict[str, uuid.UUID]:
    with app_session(app_engine, e.account_id) as s:
        ids = {"general": conv.get_or_create_estate(s).id,
               "belle": conv.get_or_create_property(s, e.belle).id,
               "blue": conv.get_or_create_property(s, e.blue).id}
        s.commit()
        return ids


def _post(app_engine, e, conversation_id, author_id, body) -> uuid.UUID:
    with app_session(app_engine, e.account_id) as s:
        p = posts.create_post(s, s.get(ChatConversation, conversation_id),
                              author=s.get(Membership, author_id), body=body)
        s.commit()
        return p.id


def test_sidebar_has_chat_not_conversations(app_client, estate):
    """A2"""
    owner = app_client(estate.account_id, estate.owner)
    r = owner.get("/")
    assert r.status_code == 200
    assert 'href="/chat/"' in r.text and 'id="nav-chat-badge"' in r.text
    assert 'href="/conversations/"' not in r.text
    assert owner.get("/conversations/").status_code == 200


def test_every_chat_route_declares_chat_use():
    """A20"""
    from mihomes.authz.actions import Access
    from mihomes.web.app import create_app

    chat_routes = [r for r in create_app().routes if getattr(r, "path", "").startswith("/chat")]
    assert len(chat_routes) >= 12
    for route in chat_routes:
        assert getattr(route.endpoint, ACTION_ATTR, None) == "chat.use", route.path
        assert getattr(route.endpoint, ACCESS_ATTR, None) == Access.ACCOUNT, route.path


def test_lazy_creation_is_idempotent(app_client, app_engine, estate):
    """A21"""
    owner = app_client(estate.account_id, estate.owner)
    assert owner.get("/chat/").status_code == 200
    assert owner.get("/chat/").status_code == 200
    with app_session(app_engine, estate.account_id) as s:
        kinds = dict(s.execute(text("SELECT kind, count(*) FROM chat_conversations "
                                    "WHERE account_id = :a GROUP BY kind"),
                               {"a": estate.account_id}).all())
    assert kinds == {"estate": 1, "property": 2}


def test_dm_rules(app_client, estate):
    """A22"""
    e = estate
    owner = app_client(e.account_id, e.owner)
    staff = app_client(e.account_id, e.staff_belle)
    hx = {"HX-Request": "true"}
    assert owner.post("/chat/dm", data={"membership_id": str(e.owner)}, headers=hx).status_code == 400
    assert owner.post("/chat/dm", data={"membership_id": str(e.revoked)},
                      headers=hx).status_code == 400
    one = owner.post("/chat/dm", data={"membership_id": str(e.staff_belle)}, headers=hx)
    two = staff.post("/chat/dm", data={"membership_id": str(e.owner)}, headers=hx)
    assert one.status_code == two.status_code == 204
    assert one.headers["HX-Redirect"] == two.headers["HX-Redirect"]
    assert staff.get(one.headers["HX-Redirect"]).status_code == 200


def test_body_is_escaped_and_safely_linked(app_client, app_engine, estate):
    """A23"""
    e = estate
    c = _groups(app_engine, e)
    owner = app_client(e.account_id, e.owner)
    hostile = ('<script>alert(1)</script> "><img src=x onerror=alert(2)> '
               'javascript:alert(3) see https://example.com/a?b=1&c=2.')
    r = owner.post(f"/chat/c/{c['general']}/posts", data={"body": hostile})
    assert r.status_code == 200
    for page in (r.text, owner.get(f"/chat/c/{c['general']}").text):
        assert "<script>alert(1)" not in page
        assert "<img src=x" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
        assert 'href="javascript:' not in page
        assert ('<a href="https://example.com/a?b=1&amp;c=2" target="_blank" '
                'rel="noopener noreferrer nofollow"') in page


def test_unread_badge(app_client, app_engine, estate):
    """A24 — counts the viewer's visible conversations only."""
    e = estate
    c = _groups(app_engine, e)
    _post(app_engine, e, c["general"], e.owner, "general one")
    _post(app_engine, e, c["belle"], e.owner, "belle one")
    _post(app_engine, e, c["blue"], e.owner, "blue one")      # invisible to staff_belle
    _post(app_engine, e, c["blue"], e.owner, "blue two")
    badge = app_client(e.account_id, e.staff_belle).get("/chat/unread-badge")
    assert badge.status_code == 200
    assert re.search(r">\s*2\s*<", badge.text), badge.text

    owner_badge = app_client(e.account_id, e.owner).get("/chat/unread-badge")
    assert not re.search(r">\s*\d+\s*<", owner_badge.text)   # own posts never count


def test_post_commits_before_response(app_client, _pg_engine, app_engine, estate):
    """A25 — by the time the response arrives, the post and its event are committed (D10)."""
    e = estate
    c = _groups(app_engine, e)
    r = app_client(e.account_id, e.staff_belle).post(f"/chat/c/{c['belle']}/posts",
                                                      data={"body": "Pool pump is noisy"})
    assert r.status_code == 200 and "Pool pump is noisy" in r.text
    post_id = re.search(r'id="post-([0-9a-f-]{36})"', r.text).group(1)
    with _pg_engine.connect() as conn:   # a different connection: only committed rows are visible
        events = conn.execute(text("SELECT type FROM chat_events WHERE post_id = :p"),
                              {"p": post_id}).scalars().all()
    assert events == ["post.created"]


def test_read_marks_and_triggers_badge(app_client, app_engine, estate):
    e = estate
    c = _groups(app_engine, e)
    _post(app_engine, e, c["general"], e.owner, "hello")
    staff = app_client(e.account_id, e.staff_none)
    r = staff.post(f"/chat/c/{c['general']}/read", data={"seq": "1"})
    assert r.status_code == 204 and r.headers["HX-Trigger"] == "chat-read"
    assert not re.search(r">\s*\d+\s*<", staff.get("/chat/unread-badge").text)


# --- A27: the derived probe -------------------------------------------------------------------

def _payloads(general_seq: int) -> dict[tuple[str, str], dict]:
    """A valid request for every id-bearing chat route, so a 422 cannot pass for a 404."""
    return {
        ("GET", "/chat/c/{conversation_id}"): {},
        ("GET", "/chat/c/{conversation_id}/posts"): {"params": {"before": general_seq + 1}},
        ("GET", "/chat/posts/{post_id}"): {},
        ("POST", "/chat/c/{conversation_id}/posts"): {"data": {"body": "intruding"}},
        ("POST", "/chat/c/{conversation_id}/read"): {"data": {"seq": "1"}},
        ("POST", "/chat/c/{conversation_id}/typing"): {},
        ("POST", "/chat/posts/{post_id}/edit"): {"data": {"body": "edited by intruder"}},
        ("POST", "/chat/posts/{post_id}/delete"): {},
        ("POST", "/chat/posts/{post_id}/react"): {"data": {"emoji": "👍"}},
        ("GET", "/chat/file/{attachment_id}"): {},
    }


def test_every_id_route_enforces_visibility(app_client, app_engine, estate):
    """A27 — staff scoped to Blue, every id-bearing route, a Belle id: 404 and no content."""
    from mihomes.web.app import create_app

    e = estate
    c = _groups(app_engine, e)
    secret = "Belle's alarm code is 4417"
    post_id = _post(app_engine, e, c["belle"], e.owner, secret)
    with app_session(app_engine, e.account_id) as s:   # a Belle attachment (SPEC-013)
        from mihomes.storage import build_key

        att = ChatAttachment(account_id=e.account_id, post_id=post_id,
                             storage_key=build_key(e.account_id, "chat", "x.png"),
                             filename="Belle-plan.png", content_type="image/png", size_bytes=1)
        s.add(att)
        s.commit()
        att_id = att.id

    derived = {
        (method, route.path)
        for route in create_app().routes
        if getattr(route, "path", "").startswith("/chat")
        and any(p in route.path for p in _ID_PARAMS)
        for method in route.methods - {"HEAD"}
    }
    table = _payloads(general_seq=1)
    assert len(derived) >= 9                       # the guard on the guard
    assert set(table) == derived, "a chat route was added or removed — extend the table"

    intruder = app_client(e.account_id, e.staff_blue)
    for (method, path), kwargs in sorted(table.items()):
        url = (path.replace("{conversation_id}", str(c["belle"]))
                   .replace("{post_id}", str(post_id))
                   .replace("{attachment_id}", str(att_id)))
        r = intruder.request(method, url, **kwargs)
        assert r.status_code == 404, (method, path, r.status_code, r.text[:200])
        assert secret not in r.text and "Belle" not in r.text, (method, path)

    with app_session(app_engine, e.account_id) as s:
        post = s.get(ChatPost, post_id)
        assert post.body == secret and post.deleted_at is None and post.edited_at is None
        assert s.get(ChatConversation, c["belle"]).next_seq == 2   # nothing was posted
