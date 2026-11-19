"""Leave a household / delete my profile (2026-11-09) — on the app role, RLS live.

The non-owner half of account deletion. Outcomes are checked through the superuser connection.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.integration.chat_helpers import app_client, estate

__all__ = ["app_client", "estate"]


def _user_of(_pg_engine, membership_id):
    with _pg_engine.connect() as c:
        return c.execute(text("SELECT user_id FROM memberships WHERE id = :m"),
                         {"m": membership_id}).scalar_one()


def _status(_pg_engine, membership_id):
    with _pg_engine.connect() as c:
        return c.execute(text("SELECT status FROM memberships WHERE id = :m"),
                         {"m": membership_id}).scalar_one()


def _staff_record(_pg_engine, account_id, user_id):
    sid = uuid.uuid4()
    with _pg_engine.begin() as c:
        c.execute(text("INSERT INTO staff (id, account_id, name, slug, role, active, user_id) "
                       "VALUES (:i, :a, 'Rosa', :s, 'HOUSEKEEPER', true, :u)"),
                  {"i": sid, "a": account_id, "s": f"rosa-{sid.hex[:6]}", "u": user_id})
    return sid


def test_staff_leave_the_household(_pg_engine, app_client, estate):
    e = estate
    user = _user_of(_pg_engine, e.staff_belle)
    staff_id = _staff_record(_pg_engine, e.account_id, user)
    client = app_client(e.account_id, e.staff_belle)

    page = client.get("/settings/connections").text
    assert 'action="/settings/leave"' in page and 'action="/settings/delete-profile"' in page

    r = client.post("/settings/leave", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/onboarding/"
    assert _status(_pg_engine, e.staff_belle) == "revoked"
    with _pg_engine.connect() as c:
        assert c.execute(text("SELECT user_id FROM staff WHERE id = :i"), {"i": staff_id}).scalar() is None
        assert c.execute(text("SELECT count(*) FROM sessions WHERE user_id = :u AND "
                              "current_account_id = :a"), {"u": user, "a": e.account_id}).scalar() == 0
        c.execute(text("SELECT 1"))
    with _pg_engine.begin() as c:
        c.execute(text("DELETE FROM staff WHERE id = :i"), {"i": staff_id})


def test_the_owner_cannot_leave_or_delete_their_profile(_pg_engine, app_client, estate):
    e = estate
    owner = app_client(e.account_id, e.owner)
    page = owner.get("/settings/connections").text
    assert 'action="/settings/leave"' not in page
    assert "You own this household" in page and 'action="/settings/delete-profile"' not in page

    r = owner.post("/settings/leave")
    assert r.status_code == 400 and "You own this household" in r.text
    r = owner.post("/settings/delete-profile", data={"confirm": "DELETE"})
    assert r.status_code == 400 and "You own a household" in r.text
    assert _status(_pg_engine, e.owner) == "active"


def test_delete_my_profile_leaves_every_household_and_erases_the_login(
        _pg_engine, app_client, estate, account_b):
    e = estate
    user = _user_of(_pg_engine, e.staff_blue)
    other = uuid.uuid4()
    with _pg_engine.begin() as c:
        c.execute(text("INSERT INTO memberships (id, user_id, account_id, role, status, created_at) "
                       "VALUES (:i, :u, :a, 'staff', 'active', now())"),
                  {"i": other, "u": user, "a": account_b})
    client = app_client(e.account_id, e.staff_blue)

    wrong = client.post("/settings/delete-profile", data={"confirm": "delete me"})
    assert wrong.status_code == 400 and _status(_pg_engine, e.staff_blue) == "active"

    r = client.post("/settings/delete-profile", data={"confirm": "DELETE"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert _status(_pg_engine, e.staff_blue) == "revoked"
    assert _status(_pg_engine, other) == "revoked", "every household is left"
    with _pg_engine.connect() as c:
        email, name, pw, google = c.execute(text(
            "SELECT email, name, password_hash, google_sub FROM users WHERE id = :u"), {"u": user}).one()
        sessions = c.execute(text("SELECT count(*) FROM sessions WHERE user_id = :u"), {"u": user}).scalar()
    assert email.startswith("deleted-") and name is None and pw is None and google is None
    assert sessions == 0
    with _pg_engine.begin() as c:
        c.execute(text("DELETE FROM memberships WHERE id = :i"), {"i": other})


def test_purge_scrubs_a_member_whose_other_households_were_all_left(
        _pg_engine, app_engine, estate, account_b):
    """A revoked membership elsewhere no longer keeps a login alive after a purge."""
    from datetime import UTC, datetime, timedelta

    from mihomes.services.privacy.deletion import purge_due, request_deletion
    from tests.integration.chat_helpers import app_session

    e = estate
    user = _user_of(_pg_engine, e.staff_none)
    left = uuid.uuid4()
    with _pg_engine.begin() as c:
        c.execute(text("INSERT INTO memberships (id, user_id, account_id, role, status, created_at) "
                       "VALUES (:i, :u, :a, 'staff', 'revoked', now())"),
                  {"i": left, "u": user, "a": account_b})
    now = datetime.now(UTC)
    try:
        with app_session(app_engine, e.account_id) as s:
            request_deletion(s, e.account_id, user, now=now - timedelta(days=31))
            s.commit()
        with app_session(app_engine, e.account_id) as s:
            assert purge_due(s, e.account_id, now=now).status == "purged"
            s.commit()
        with _pg_engine.connect() as c:
            email = c.execute(text("SELECT email FROM users WHERE id = :u"), {"u": user}).scalar()
        assert email.startswith("deleted-")
    finally:
        with _pg_engine.begin() as c:
            c.execute(text("DELETE FROM memberships WHERE id = :i"), {"i": left})
            c.execute(text("DELETE FROM account_deletion_requests WHERE account_id = :a"),
                      {"a": e.account_id})
