"""Deactivating and reactivating a household (2026-11-06) — on the app role, RLS live.

Slice 2 of the 2026-09-23 plan plus Slice 4's 90-day rule. Outcomes are checked through the
superuser connection.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from tests.integration.chat_helpers import app_client, app_session, estate, sign_in

__all__ = ["app_client", "estate"]

NOW = datetime.now(UTC)


def _deactivated_at(_pg_engine, account_id):
    with _pg_engine.connect() as c:
        return c.execute(text("SELECT deactivated_at FROM accounts WHERE id = :a"),
                         {"a": account_id}).scalar()


@pytest.fixture
def clean_requests(_pg_engine, estate):
    yield
    with _pg_engine.begin() as c:
        c.execute(text("DELETE FROM account_deletion_requests WHERE account_id = :a"),
                  {"a": estate.account_id})


def test_owner_deactivates_and_everyone_lands_on_the_deactivated_page(_pg_engine, app_client, estate):
    e = estate
    staff_cookie = sign_in(_pg_engine, e.account_id, e.staff_belle)
    owner = app_client(e.account_id, e.owner)

    r = owner.post("/settings/deactivate", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/onboarding/"
    assert _deactivated_at(_pg_engine, e.account_id) is not None
    with _pg_engine.connect() as c:
        bound = c.execute(text("SELECT count(*) FROM sessions WHERE current_account_id = :a"),
                          {"a": e.account_id}).scalar()
    assert bound == 0, "every session in the household is detached"

    page = owner.get("/onboarding/").text
    assert "This household is switched off" in page and 'action="/onboarding/reactivate"' in page

    staff = app_client(e.account_id, e.staff_belle)
    staff.cookies.set("mihomes_session", staff_cookie)
    staff_page = staff.get("/onboarding/").text
    assert "Deactivated by the owner" in staff_page
    assert 'action="/onboarding/reactivate"' not in staff_page

    refused = staff.post("/onboarding/reactivate", data={"account_id": str(e.account_id)},
                         follow_redirects=False)
    assert refused.status_code == 403, "a staff member posting the id gets no household"
    assert _deactivated_at(_pg_engine, e.account_id) is not None

    back = owner.post("/onboarding/reactivate", data={"account_id": str(e.account_id)},
                      follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == "/"
    assert _deactivated_at(_pg_engine, e.account_id) is None
    assert owner.get("/").status_code == 200, "the owner is back in"


def test_signing_in_to_a_deactivated_household_is_refused(_pg_engine, app_engine, estate):
    from mihomes.auth.sessions import set_current_account

    e = estate
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE accounts SET deactivated_at = now() WHERE id = :a"), {"a": e.account_id})
        user_id = c.execute(text("SELECT user_id FROM memberships WHERE id = :m"), {"m": e.owner}).scalar()
        sid = uuid.uuid4()
        c.execute(text("INSERT INTO sessions (id, session_id_hash, user_id, created_at, expires_at) "
                       "VALUES (:i, :h, :u, now(), now() + interval '1 day')"),
                  {"i": sid, "h": uuid.uuid4().hex, "u": user_id})
    with app_session(app_engine, e.account_id) as s:
        assert set_current_account(s, sid, e.account_id) is False


def test_a_purged_household_cannot_be_reactivated(_pg_engine, app_engine, estate, clean_requests):
    from mihomes.services.account_lifecycle import LifecycleRefused, reactivate

    e = estate
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE accounts SET deactivated_at = now() WHERE id = :a"), {"a": e.account_id})
        c.execute(text("INSERT INTO account_deletion_requests (id, account_id, requested_at, "
                       "requested_by_user_id, purge_after, purged_at) VALUES "
                       "(:i, :a, now(), :u, now(), now())"),
                  {"i": uuid.uuid4(), "a": e.account_id, "u": uuid.UUID(int=0)})
        owner_user = c.execute(text("SELECT user_id FROM memberships WHERE id = :m"), {"m": e.owner}).scalar()
    with app_session(app_engine, e.account_id) as s:
        with pytest.raises(LifecycleRefused):
            reactivate(s, e.account_id, owner_user)


def test_the_bot_ignores_a_deactivated_household(_pg_engine, app_engine, estate):
    from mihomes.services.gateways.chat_binding import bound_account_for_chat
    from mihomes.services.gateways.identity import UnlinkedSender, resolve_sender

    e = estate
    tg_user, chat = 7_000_000_000 + uuid.uuid4().int % 1_000_000, -(10**12 + uuid.uuid4().int % 10**6)
    with _pg_engine.begin() as c:
        c.execute(text("INSERT INTO telegram_links (id, account_id, membership_id, telegram_user_id, linked_at) "
                       "VALUES (:i, :a, :m, :t, now())"),
                  {"i": uuid.uuid4(), "a": e.account_id, "m": e.owner, "t": tg_user})
        c.execute(text("INSERT INTO telegram_chat_bindings (id, account_id, chat_id, bound_at) "
                       "VALUES (:i, :a, :c, now())"), {"i": uuid.uuid4(), "a": e.account_id, "c": chat})
    try:
        with app_engine.connect() as conn:
            from sqlalchemy.orm import Session

            with Session(bind=conn) as s:
                assert resolve_sender(s, gateway="telegram", sender_id=str(tg_user)).account_id == e.account_id
                assert bound_account_for_chat(s, str(chat)) == e.account_id
        with _pg_engine.begin() as c:
            c.execute(text("UPDATE accounts SET deactivated_at = now() WHERE id = :a"), {"a": e.account_id})
        with app_engine.connect() as conn:
            from sqlalchemy.orm import Session

            with Session(bind=conn) as s:
                with pytest.raises(UnlinkedSender):
                    resolve_sender(s, gateway="telegram", sender_id=str(tg_user))
                assert bound_account_for_chat(s, str(chat)) is None
    finally:
        with _pg_engine.begin() as c:
            c.execute(text("DELETE FROM telegram_links WHERE telegram_user_id = :t"), {"t": tg_user})
            c.execute(text("DELETE FROM telegram_chat_bindings WHERE chat_id = :c"), {"c": chat})


def test_sweeps_skip_a_deactivated_household_but_the_purge_job_does_not(_pg_engine, estate, monkeypatch):
    from contextlib import contextmanager

    from sqlalchemy.orm import Session

    import mihomes.db
    from mihomes.cli.jobs import _all_accounts

    @contextmanager
    def pg_session():  # `_all_accounts` opens the app's default DB; point it at the test Postgres
        with Session(_pg_engine) as s:
            yield s

    monkeypatch.setattr(mihomes.db, "get_session", pg_session)
    e = estate
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE accounts SET deactivated_at = now() WHERE id = :a"), {"a": e.account_id})
    assert e.account_id not in {a for a, _ in _all_accounts()}
    assert e.account_id in {a for a, _ in _all_accounts(include_deactivated=True)}


def test_ninety_day_rule_erases_once(_pg_engine, app_engine, estate, clean_requests):
    from mihomes.services.privacy.deletion import purge_due

    e = estate
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE accounts SET deactivated_at = :d WHERE id = :a"),
                  {"d": NOW - timedelta(days=89), "a": e.account_id})
    with app_session(app_engine, e.account_id) as s:
        assert purge_due(s, e.account_id, now=NOW) is None, "89 days: not yet"

    with _pg_engine.begin() as c:
        c.execute(text("UPDATE accounts SET deactivated_at = :d WHERE id = :a"),
                  {"d": NOW - timedelta(days=91), "a": e.account_id})
    with app_session(app_engine, e.account_id) as s:
        dry = purge_due(s, e.account_id, now=NOW, dry_run=True)
        s.commit()
    assert dry.status == "would_purge"
    assert _pg_engine.connect().execute(text(
        "SELECT count(*) FROM account_deletion_requests WHERE account_id = :a"), {"a": e.account_id}).scalar() == 0

    with app_session(app_engine, e.account_id) as s:
        assert purge_due(s, e.account_id, now=NOW).status == "purged"
        s.commit()
    with _pg_engine.connect() as c:
        rows = c.execute(text("SELECT requested_by_user_id, purged_at FROM account_deletion_requests "
                              "WHERE account_id = :a"), {"a": e.account_id}).all()
        props = c.execute(text("SELECT count(*) FROM properties WHERE account_id = :a"),
                          {"a": e.account_id}).scalar()
    assert len(rows) == 1 and rows[0].requested_by_user_id == uuid.UUID(int=0) and rows[0].purged_at
    assert props == 0

    with app_session(app_engine, e.account_id) as s:  # purged, then 90 more days: never again
        assert purge_due(s, e.account_id, now=NOW + timedelta(days=200)) is None
    assert _pg_engine.connect().execute(text(
        "SELECT count(*) FROM account_deletion_requests WHERE account_id = :a"), {"a": e.account_id}).scalar() == 1
