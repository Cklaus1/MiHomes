"""Running deletions when the grace period ends (2026-11-06) — on the app role, with RLS live.

`purge` existed and was tested as the superuser, but nothing called it, so a requested deletion
never happened. These tests drive `purge_due` (what `mihomes jobs purge-deletions` runs) as the
non-superuser role, and check the outcome through the superuser connection: a purge that
reports success while RLS hid every row is the failure that matters most here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from mihomes.services.privacy.deletion import (
    PurgeRefused,
    cancel_deletion,
    purge,
    purge_due,
    request_deletion,
)
from tests.integration.chat_helpers import app_session, estate

__all__ = ["estate"]

NOW = datetime.now(UTC)


def _request(app_engine, e, *, days_ago: int):
    """A deletion request made `days_ago` days ago (30-day grace)."""
    with app_session(app_engine, e.account_id) as s:
        req = request_deletion(s, e.account_id, e.user_ids[0], now=NOW - timedelta(days=days_ago))
        s.commit()
        return req.id


def _count(_pg_engine, sql, **params) -> int:
    with _pg_engine.connect() as c:
        return c.execute(text(sql), params).scalar()


@pytest.fixture
def clean_requests(_pg_engine, estate):
    yield
    with _pg_engine.begin() as c:
        c.execute(text("DELETE FROM account_deletion_requests WHERE account_id = :a"),
                  {"a": estate.account_id})


def _owner_user(_pg_engine, e):
    with _pg_engine.connect() as c:
        return c.execute(text("SELECT user_id FROM memberships WHERE id = :m"),
                         {"m": e.owner}).scalar_one()


def test_not_due_and_cancelled_are_untouched(_pg_engine, app_engine, estate, clean_requests):
    e = estate
    _request(app_engine, e, days_ago=5)
    with app_session(app_engine, e.account_id) as s:
        assert purge_due(s, e.account_id, now=NOW) is None, "inside the 30 days"
        cancel_deletion(s, e.account_id)
        s.commit()
    with app_session(app_engine, e.account_id) as s:
        assert purge_due(s, e.account_id, now=NOW + timedelta(days=60)) is None, "cancelled"
    assert _count(_pg_engine, "SELECT count(*) FROM properties WHERE account_id = :a",
                  a=e.account_id) == 2


def test_dry_run_reports_and_erases_nothing(_pg_engine, app_engine, estate, clean_requests):
    e = estate
    _request(app_engine, e, days_ago=31)
    with app_session(app_engine, e.account_id) as s:
        outcome = purge_due(s, e.account_id, now=NOW, dry_run=True)
        s.commit()
    assert outcome.status == "would_purge"
    assert _count(_pg_engine, "SELECT count(*) FROM properties WHERE account_id = :a",
                  a=e.account_id) == 2


def test_due_deletion_erases_on_the_app_role(_pg_engine, app_engine, estate, account_b, clean_requests):
    e = estate
    owner_user = _owner_user(_pg_engine, e)
    with _pg_engine.begin() as c:  # the owner also belongs to another estate: keep their sign-in
        c.execute(text("INSERT INTO memberships (id, user_id, account_id, role, status, created_at) "
                       "VALUES (:i, :u, :a, 'owner', 'active', now())"),
                  {"i": uuid.uuid4(), "u": owner_user, "a": account_b})
    _request(app_engine, e, days_ago=31)

    with app_session(app_engine, e.account_id) as s:
        outcome = purge_due(s, e.account_id, now=NOW)
        s.commit()
    assert outcome.status == "purged", outcome

    a = e.account_id
    for table in ("properties", "memberships"):
        assert _count(_pg_engine, f"SELECT count(*) FROM {table} WHERE account_id = :a", a=a) == 0, table
    assert _count(_pg_engine, "SELECT count(*) FROM account_deletion_requests "
                              "WHERE account_id = :a AND purged_at IS NOT NULL", a=a) == 1
    with _pg_engine.connect() as c:
        name, plan, deactivated = c.execute(text(
            "SELECT name, plan, deactivated_at FROM accounts WHERE id = :a"), {"a": a}).one()
    assert (name, plan) == ("Deleted account", "free") and deactivated is not None

    staff_only = [u for u in e.user_ids if u != owner_user]
    emails = dict(_pg_engine.connect().execute(text(
        "SELECT id, email FROM users WHERE id = ANY(:u)"), {"u": e.user_ids}).all())
    assert all(emails[u].startswith("deleted-") for u in staff_only), "sole-estate members scrubbed"
    assert not emails[owner_user].startswith("deleted-"), "a member of another estate is kept"

    with app_session(app_engine, e.account_id) as s:
        assert purge_due(s, e.account_id, now=NOW) is None, "a purged request is not due again"
    with _pg_engine.begin() as c:
        c.execute(text("DELETE FROM memberships WHERE account_id = :b"), {"b": account_b})


def test_billing_failure_skips_the_account(_pg_engine, app_engine, estate, clean_requests):
    e = estate
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE accounts SET stripe_subscription_id = 'sub_x', "
                       "subscription_status = 'active' WHERE id = :a"), {"a": e.account_id})
    _request(app_engine, e, days_ago=31)

    class Down:
        def cancel(self, **_):
            raise RuntimeError("stripe unreachable")

    with app_session(app_engine, e.account_id) as s:
        outcome = purge_due(s, e.account_id, now=NOW, billing=Down())
        s.commit()
    assert outcome.status == "billing_failed"
    assert _count(_pg_engine, "SELECT count(*) FROM properties WHERE account_id = :a",
                  a=e.account_id) == 2
    assert _count(_pg_engine, "SELECT count(*) FROM account_deletion_requests WHERE account_id = :a "
                              "AND purged_at IS NULL AND cancelled_at IS NULL", a=e.account_id) == 1


def test_purge_refuses_when_it_cannot_bind(app_engine, estate, account_b, monkeypatch):
    """Bound to another account and unable to rebind: refuse, never "erase" zero rows."""
    import mihomes.tenancy.connection as connection

    monkeypatch.setattr(connection, "bind_account_guc", lambda *a, **k: None)
    with app_session(app_engine, account_b) as s:
        with pytest.raises(PurgeRefused):
            purge(s, SimpleNamespace(account_id=estate.account_id))
