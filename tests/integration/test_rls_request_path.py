"""The authenticated request path, exercised as a **non-superuser** so RLS actually applies.

**Why this file exists.** Six membership reads across three modules run *before* a tenant is
bound — session lookup, principal resolution, sign-in's account binding, account
re-verification — and five of them were missing the `app.current_user` GUC that RLS's
`membership_self` policy keys on. The reads returned zero rows for a user with a perfectly good
membership, and the symptoms did not look like one bug:

  * `lookup_session` returned `None`, so every request read as unauthenticated and **the
    browser bounced back to `/login` in a loop** — including immediately after a successful
    sign-in.
  * `resolve_principal` found nothing and raised 403, leaving the user **stuck on the onboarding
    wizard** with no other page reachable.
  * `sole_account_for` found nothing, so the session was never bound to an account at sign-in.
  * `/alerts/badge` answered 403 on a timer, flooding the log.

**Every one was invisible to the other 2800 tests**, because `conftest`'s fixtures connect as
`postgres` — a superuser, to whom RLS does not apply at all (`rls.py` records that
measurement). The policies were present in every test database and bound to nothing.

So this file is not "more coverage of the same paths". It is the *only* place the suite drives a
real request as a role the policies apply to, which is the shape of every install that is not a
test. It asserts RLS is enforced before it asserts anything else, so it cannot quietly stop
testing what it exists to test.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PASSWORD = "correct horse battery staple"

_HTML = {
    "Accept": "text/html",
    "Origin": "http://localhost:5000",
    "Sec-Fetch-Site": "same-origin",
}


@pytest.fixture
def rls_app(_pg_engine):
    """An app whose engine connects as a **non-superuser**, with one owner and one property.

    Yields `(TestClient, account_id)`. Onboarding is marked finished so the client lands on the
    dashboard rather than the wizard — the state a returning user is in, which is precisely the
    state the login loop broke.
    """
    from sqlalchemy.orm import Session as OrmSession

    from tests.conftest import APP_PASSWORD, APP_ROLE

    name = f"mihomes_rlsreq{uuid.uuid4().hex[:8]}"
    admin = create_engine(
        str(_pg_engine.url.set(database="postgres")), isolation_level="AUTOCOMMIT", future=True
    )
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')

    owner_url = str(_pg_engine.url.set(database=name))
    owner = create_engine(owner_url, future=True)
    account_id = uuid.uuid4()
    try:
        from mihomes.auth.passwords import hash_password
        from mihomes.models import Base
        from mihomes.services import onboarding_service as onb

        Base.metadata.create_all(owner)

        with owner.begin() as conn:
            enforced = conn.execute(
                text(
                    "select relrowsecurity, relforcerowsecurity "
                    "from pg_class where relname = 'memberships'"
                )
            ).one()
            assert enforced == (True, True), (
                "RLS is not enforced in this fixture — every assertion below would pass "
                "against policies bound to nothing, which is the exact blind spot this file "
                "exists to close"
            )

            user_id = uuid.uuid4()
            conn.execute(
                text(
                    "insert into accounts (id, slug, name, type, plan, subscription_status) "
                    "values (:a, 'belle', 'Old Name', 'household', 'estate', 'active')"
                ),
                {"a": account_id},
            )
            conn.execute(
                text(
                    "insert into users (id, email, name, password_hash, password_set_at, "
                    "created_at) values (:u, 'owner@example.com', 'Owner', :h, now(), now())"
                ),
                {"u": user_id, "h": hash_password(PASSWORD)},
            )
            conn.execute(
                text("select set_config('app.current_account', :a, true)"),
                {"a": str(account_id)},
            )
            conn.execute(
                text(
                    "insert into memberships (id, user_id, account_id, role, status) "
                    "values (:i, :u, :a, 'owner', 'active')"
                ),
                {"i": uuid.uuid4(), "u": user_id, "a": account_id},
            )
            conn.execute(
                text(
                    "insert into properties (id, account_id, slug, name, property_type, "
                    "status, currency, occupied, created_at) values "
                    "(:i, :a, 'main', 'Main House', 'PRIMARY', 'OPEN', 'USD', true, now())"
                ),
                {"i": uuid.uuid4(), "a": account_id},
            )

        with OrmSession(owner) as s:
            onb.complete_step(s, account_id, onb.STEP_CREATE_ACCOUNT)
            onb.complete_step(s, account_id, onb.STEP_ADD_HOME)
            onb.finish(s, account_id)
            s.commit()

        with owner.begin() as conn:
            for stmt in (
                f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                f"TO {APP_ROLE}",
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
            ):
                conn.exec_driver_sql(stmt)
    finally:
        owner.dispose()

    import mihomes.db as db_module

    app_url = str(make_url(owner_url).set(username=APP_ROLE, password=APP_PASSWORD))
    saved_engine, saved_factory = db_module._engine, db_module._SessionLocal
    db_module._engine = create_engine(app_url, future=True)
    db_module._SessionLocal = None

    from fastapi.testclient import TestClient

    from mihomes.web.app import app

    client = TestClient(app, base_url="http://localhost:5000", follow_redirects=False)
    try:
        yield client, account_id
    finally:
        db_module._engine.dispose()
        db_module._engine, db_module._SessionLocal = saved_engine, saved_factory
        with admin.connect() as conn:
            conn.exec_driver_sql(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{name}' AND pid <> pg_backend_pid()"
            )
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}"')
        admin.dispose()


def _sign_in(client):
    return client.post(
        "/login",
        data={"email": "owner@example.com", "password": PASSWORD},
        headers=_HTML,
    )


def test_sign_in_lands_on_the_dashboard_not_the_wizard(rls_app):
    """`destination_for` reads memberships; unbound, it sent a returning owner to onboarding."""
    client, _ = rls_app
    response = _sign_in(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/", (
        "an existing member was sent somewhere other than the dashboard after sign-in"
    )


def test_the_session_survives_the_next_request(rls_app):
    """**The login loop.**

    `lookup_session` re-reads the membership on *every* request. Unbound, it returned `None`,
    the request read as unauthenticated, and the browser was sent back to `/login` — from which
    signing in again produced the same thing. A 303 to `/login` here is that loop.
    """
    client, _ = rls_app
    _sign_in(client)

    response = client.get("/", headers=_HTML)
    assert response.status_code == 200, (
        f"the request after sign-in answered {response.status_code} "
        f"(location: {response.headers.get('location')!r}) — the session did not survive, "
        f"which is the /login loop"
    )


@pytest.mark.parametrize("path", ["/", "/settings", "/alerts/badge", "/properties/"])
def test_authenticated_pages_render(rls_app, path):
    """A spread of route classes: dashboard, ACCOUNT, a polled partial, and a COLLECTION list.

    `/alerts/badge` is here because the dashboard polls it on a timer, so its 403 arrived
    dozens of times a minute in the log and was the loudest symptom of the quietest bug.
    """
    client, _ = rls_app
    _sign_in(client)

    response = client.get(path, headers=_HTML)
    assert response.status_code == 200, (
        f"{path} answered {response.status_code} "
        f"(location: {response.headers.get('location')!r}) for a signed-in owner"
    )


def test_renaming_the_estate_works_end_to_end(rls_app):
    """The rename, including its audit row — which is itself a tenant-table write.

    With only the ContextVar bound and not the GUC, the rename committed and the audit insert
    was refused: the estate was renamed and the trail recording it was not, which is the one
    outcome an audit trail must never have.
    """
    client, account_id = rls_app
    _sign_in(client)

    response = client.post(
        "/settings/account", data={"name": "Belle Estate"}, headers=_HTML
    )
    assert response.status_code == 303, response.text

    page = client.get("/settings", headers=_HTML)
    assert page.status_code == 200
    assert "Belle Estate" in page.text

    import mihomes.db as db_module

    with db_module._engine.begin() as conn:
        conn.execute(
            text("select set_config('app.current_account', :a, true)"),
            {"a": str(account_id)},
        )
        assert conn.execute(text("select name from accounts")).scalar() == "Belle Estate"
        assert conn.execute(text("select slug from accounts")).scalar() == "belle", (
            "the slug changed — every `--account belle` invocation now fails"
        )
        rename_rows = conn.execute(
            text("select count(*) from audit_log where action = 'rename'")
        ).scalar()
        assert rename_rows == 1, "the rename committed without an audit row"
