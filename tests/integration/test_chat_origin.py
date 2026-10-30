"""SPEC-014 A32 — chat POSTs are covered by the Origin guard; same-origin HTMX passes."""

from __future__ import annotations

from tests.integration.chat_helpers import app_client, app_session, estate

__all__ = ["app_client", "estate"]  # re-exported pytest fixtures (see chat_helpers)


def _general(app_engine, e):
    from mihomes.services.chat import conversations as conv

    with app_session(app_engine, e.account_id) as s:
        cid = conv.get_or_create_estate(s).id
        s.commit()
        return cid


def test_cross_origin_post_blocked(app_client, app_engine, estate):
    e = estate
    cid = _general(app_engine, e)
    c = app_client(e.account_id, e.owner)
    for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"},
                    {"Origin": "null"}):
        r = c.post(f"/chat/c/{cid}/posts", data={"body": "csrf"}, headers=headers)
        assert r.status_code == 403, headers


def test_htmx_post_with_origin_allowed(app_client, app_engine, estate):
    e = estate
    cid = _general(app_engine, e)
    c = app_client(e.account_id, e.owner)
    r = c.post(f"/chat/c/{cid}/posts", data={"body": "same origin"},
               headers={"HX-Request": "true", "Origin": "http://localhost",
                        "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and "same origin" in r.text
