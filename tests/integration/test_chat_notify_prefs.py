"""SPEC-014 A28 — staff can open and save their own chat-notification preferences."""

from __future__ import annotations

from sqlalchemy import text

from tests.integration.chat_helpers import app_client, estate

__all__ = ["app_client", "estate"]  # re-exported pytest fixtures (see chat_helpers)


def test_staff_can_save_own_prefs(app_client, _pg_engine, estate):
    e = estate
    c = app_client(e.account_id, e.staff_belle)
    page = c.get("/chat/notifications")
    assert page.status_code == 200 and "Chat notifications" in page.text
    assert "checked" in page.text                         # defaults: both on

    # A forged membership_id in the form is ignored: the route only reads the principal.
    r = c.post("/chat/notifications", data={"email_digest": "1", "mute_hours": "8",
                                            "membership_id": str(e.owner)})
    assert r.status_code == 200 and "Saved." in r.text and "Paused until" in r.text
    with _pg_engine.connect() as conn:
        rows = conn.execute(text("SELECT membership_id, email_digest, push, muted_until IS NOT NULL "
                                 "FROM chat_notify_prefs WHERE account_id = :a"),
                            {"a": e.account_id}).all()
    assert [tuple(r) for r in rows] == [(e.staff_belle, True, False, True)]

    assert c.post("/chat/notifications", data={"mute_hours": "5"}).status_code == 400

    # The settings page staff can reach loads the partial.
    connections = c.get("/settings/connections")
    assert connections.status_code == 200 and 'hx-get="/chat/notifications"' in connections.text
