"""SPEC-014 A29–A31 — flood limits and the body limit, through the real app on the app role."""

from __future__ import annotations

import re

from sqlalchemy import text

from tests.integration.chat_helpers import app_client, app_session, estate

__all__ = ["app_client", "estate"]  # re-exported pytest fixtures (see chat_helpers)


def _general(app_engine, e):
    from mihomes.services.chat import conversations as conv

    with app_session(app_engine, e.account_id) as s:
        cid = conv.get_or_create_estate(s).id
        s.commit()
        return cid


def _count(_pg_engine, cid) -> int:
    with _pg_engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM chat_posts WHERE conversation_id = :c"),
                         {"c": cid}).scalar()


def test_post_over_limit_429_keeps_composer(app_client, app_engine, _pg_engine, estate,
                                            monkeypatch):
    """A29 — the 21st post in a minute: 429, Retry-After, plain text, no HX-Trigger, no row."""
    from mihomes.services.chat import ai_bridge

    monkeypatch.setattr(ai_bridge, "dispatch", lambda *a, **k: None)
    e = estate
    cid = _general(app_engine, e)
    c = app_client(e.account_id, e.staff_belle)
    for i in range(20):
        assert c.post(f"/chat/c/{cid}/posts", data={"body": f"m{i}"}).status_code == 200
    r = c.post(f"/chat/c/{cid}/posts", data={"body": "the 21st"})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert r.headers["content-type"].startswith("text/plain")
    assert "hx-trigger" not in {k.lower() for k in r.headers}
    assert "<li" not in r.text and "post-" not in r.text      # no partial for htmx to swap
    assert _count(_pg_engine, cid) == 20
    # The composer only resets on a successful request (B2).
    from pathlib import Path

    composer = (Path(__file__).resolve().parents[2] / "src/mihomes/web/templates/partials/chat/"
                "_composer.html").read_text(encoding="utf-8")
    assert re.search(r"after-request=\"if\(event\.detail\.successful\)\{ this\.reset\(\)", composer)


def test_reaction_limit_per_membership(app_client, app_engine, _pg_engine, estate):
    """A30 — the 11th reaction in 10 s is 429; another member is unaffected."""
    e = estate
    cid = _general(app_engine, e)
    owner = app_client(e.account_id, e.owner)
    r = owner.post(f"/chat/c/{cid}/posts", data={"body": "react to me"})
    pid = re.search(r'id="post-([0-9a-f-]{36})"', r.text).group(1)
    staff = app_client(e.account_id, e.staff_belle)
    for _ in range(10):
        assert staff.post(f"/chat/posts/{pid}/react", data={"emoji": "👍"}).status_code == 200
    assert staff.post(f"/chat/posts/{pid}/react", data={"emoji": "👍"}).status_code == 429
    assert owner.post(f"/chat/posts/{pid}/react", data={"emoji": "✅"}).status_code == 200


def test_body_limit_4000(app_client, app_engine, _pg_engine, estate):
    """A31 — 4001 characters refused with 422 and no row; exactly 4000 accepted."""
    e = estate
    cid = _general(app_engine, e)
    c = app_client(e.account_id, e.owner)
    assert c.post(f"/chat/c/{cid}/posts", data={"body": "x" * 4001}).status_code == 422
    assert _count(_pg_engine, cid) == 0
    assert c.post(f"/chat/c/{cid}/posts", data={"body": "y" * 4000}).status_code == 200
    assert _count(_pg_engine, cid) == 1
