"""SPEC-014 A26, A27 — subscribe refuses anything but a known push service; unsubscribe is own-row."""

from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.integration.chat_helpers import app_client, estate
from tests.integration.chat_notify_helpers import keys

__all__ = ["app_client", "estate"]  # re-exported pytest fixtures (see chat_helpers)


def _body(endpoint, p256dh=None, auth=None):
    k1, k2 = keys()
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh or k1, "auth": auth or k2}}


def _rows(_pg_engine, e) -> list[tuple]:
    with _pg_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT membership_id, endpoint FROM push_subscriptions WHERE account_id = :a"),
            {"a": e.account_id})]


def test_subscribe_rejects_unknown_push_host(app_client, _pg_engine, estate):
    """A26 — http, a foreign host, a port, or bad keys: 422 and no row. A real one: 204."""
    e = estate
    c = app_client(e.account_id, e.staff_belle)
    for bad in (_body("http://fcm.googleapis.com/fcm/send/x"),
                _body("https://169.254.169.254/latest"),
                _body("https://evo-dev.tail6765ef.ts.net/x"),
                _body("https://fcm.googleapis.com:444/fcm/send/x"),
                _body("https://fcm.googleapis.com/fcm/send/x", p256dh="short"),
                _body("https://fcm.googleapis.com/fcm/send/x", auth="AAAA"),
                {"nope": 1}):
        r = c.post("/chat/push/subscribe", json=bad)
        assert r.status_code == 422, bad
    assert _rows(_pg_engine, e) == []
    good = "https://fcm.googleapis.com/fcm/send/" + uuid.uuid4().hex
    assert c.post("/chat/push/subscribe", json=_body(good)).status_code == 204
    assert c.post("/chat/push/subscribe", json=_body(good)).status_code == 204   # idempotent
    assert _rows(_pg_engine, e) == [(e.staff_belle, good)]


def test_unsubscribe_only_own(app_client, _pg_engine, estate):
    """A27 — two members register the same endpoint; one unsubscribing removes only theirs."""
    e = estate
    shared = "https://web.push.apple.com/" + uuid.uuid4().hex
    staff = app_client(e.account_id, e.staff_belle)
    owner = app_client(e.account_id, e.owner)
    assert staff.post("/chat/push/subscribe", json=_body(shared)).status_code == 204
    assert owner.post("/chat/push/subscribe", json=_body(shared)).status_code == 204
    assert staff.post("/chat/push/unsubscribe", json={"endpoint": shared}).status_code == 204
    assert _rows(_pg_engine, e) == [(e.owner, shared)]
    assert staff.post("/chat/push/unsubscribe", json={"endpoint": shared}).status_code == 204
    assert _rows(_pg_engine, e) == [(e.owner, shared)]
