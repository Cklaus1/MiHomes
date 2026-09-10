"""G13.5 · `Access.SESSION` — routes that run before or across account selection.

The class exists because §4.1's `ITEM`/`COLLECTION`/`ACCOUNT` all presuppose an account, and
three screens in this phase do not have one: onboarding steps 1-2, invite acceptance, and the
switcher. Forcing them into `ACCOUNT` 403s every one, because `enforce_declared_action` resolves
an account before consulting the matrix.

**The risk of adding a class that skips the matrix is that it becomes the easy way out.** These
tests pin the two properties that keep it honest: it still requires authentication, and it is
used by a small, reviewable set of routes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import text

from mihomes.auth.sessions import SESSION_COOKIE, hash_session_id
from mihomes.authz.actions import Access
from mihomes.authz.declare import declared_action, declares_session


def _signed_in_without_account(conn) -> str:
    """A user with a valid session and **no membership anywhere** — the onboarding state."""
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, google_sub, email, name, created_at) "
            "VALUES (:id, :sub, :email, 'New Person', now())"
        ),
        {"id": user_id, "sub": f"sub-{user_id.hex[:12]}", "email": f"{user_id.hex[:6]}@ex.com"},
    )
    raw = f"onboarding-session-{uuid.uuid4().hex}"
    conn.execute(
        text(
            "INSERT INTO sessions (id, session_id_hash, user_id, current_account_id, "
            "created_at, expires_at) VALUES (:id, :h, :uid, NULL, now(), :exp)"
        ),
        {
            "id": uuid.uuid4(), "h": hash_session_id(raw), "uid": user_id,
            "exp": datetime.now(timezone.utc) + timedelta(days=1),
        },
    )
    return raw


class TestSessionRoutesReachWithoutAnAccount:
    def test_onboarding_is_reachable_before_an_account_exists(self, web_client_as):
        """The whole reason the class was added.

        Under `Access.ACCOUNT` this is a 403 — `resolve_principal` raises "No account selected"
        before the route runs — and the user is locked out of the screen that would give them an
        account. A perfect deadlock, and the sort that only shows up on a *new* signup.
        """
        client = web_client_as("owner")           # builds the app; cookie replaced below
        raw = _signed_in_without_account(web_client_as.connection)
        client.cookies.set(SESSION_COOKIE, raw)

        response = client.get("/onboarding/")
        assert response.status_code == 200, response.text
        assert "Name your household" in response.text

    def test_session_routes_still_require_authentication(self, web_client_as):
        """`SESSION` is narrower than `ACCOUNT`, **not** weaker than authenticated.

        If this ever returns 200, the class has become "public" and the exemption is a hole
        rather than a narrowing.
        """
        client = web_client_as("owner")
        client.cookies.clear()

        for path in ("/onboarding/", "/invite/some-token"):
            assert client.get(path).status_code == 401, path


class TestTheExemptionStaysSmall:
    def test_only_the_expected_routes_skip_the_matrix(self):
        """A census, because an exemption nobody counts is one that grows.

        `Access.SESSION` opts a route out of the capability matrix entirely. That is correct for
        the three screens that cannot be authorised by a role in an account — and wrong for
        anything else, so the set is pinned here rather than trusted to review.
        """
        from mihomes.web.app import create_app

        app = create_app()
        session_routes = set()
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            declared = declared_action(route.endpoint)
            if declared and declared[1] is Access.SESSION:
                session_routes.add(route.path)

        assert session_routes == {
            "/onboarding/",
            "/onboarding/account",
            "/onboarding/property",
            "/onboarding/skip",
            "/onboarding/finish",
            "/invite/{token}",
            "/invite/{token}/accept",
            "/accounts/switch",
        }, f"the Access.SESSION exemption has drifted: {sorted(session_routes)}"

    def test_every_session_route_carries_a_justification(self):
        """Same discipline as the permanent allowlist: no unexplained exemptions."""
        from mihomes.web.app import create_app

        app = create_app()
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            declared = declared_action(route.endpoint)
            if declared and declared[1] is Access.SESSION:
                reason = getattr(route.endpoint, "__mihomes_session_reason__", "")
                assert len(reason) >= 20, f"{route.path} has no real justification"

    def test_a_thin_justification_is_refused_at_import_time(self):
        """The check has teeth, or the requirement is decorative."""
        with pytest.raises(ValueError, match="real justification"):
            declares_session("because")

    def test_session_action_is_not_a_matrix_key(self):
        """It must stay out of `MATRIX`, or A1's "rows 1-20 exactly" breaks.

        And it would be a lie besides: these routes are not authorised by a role within an
        account, which is the only thing the matrix describes.
        """
        from mihomes.authz.actions import MATRIX
        from mihomes.authz.declare import SESSION_ACTION

        assert SESSION_ACTION not in MATRIX


class TestTheAccountlessDeadEnd:
    """A signed-in user with no account must never be shown a wall with nothing to click.

    **The bug.** `deps.resolve_principal` raises `403 "No account selected"` for a session whose
    `current_account_id` is NULL, and nothing translated it. `/onboarding/` rendered fine — it is
    `Access.SESSION` — but `/`, `/settings` and `/team` all answered a bare
    `{"detail":"No account selected"}`. Signed in and locked out at the same time, with the one
    screen that would fix it reachable only by typing the URL.

    The 401 handler had solved exactly this for signed-*out* visitors months earlier. 403 simply
    never got the same treatment, and the asymmetry is invisible until someone lands in it.
    """

    def test_a_browser_with_no_account_is_sent_to_onboarding(self, web_client_as):
        """The regression. A 403 here means the dead end is back."""
        client = web_client_as("owner")
        client.cookies.set(SESSION_COOKIE, _signed_in_without_account(web_client_as.connection))

        for path in ("/", "/settings", "/team"):
            response = client.get(
                path, headers={"accept": "text/html"}, follow_redirects=False
            )
            assert response.status_code == 303, (
                f"{path} answered {response.status_code} for a signed-in user with no account "
                f"— they are locked out with nowhere to click: {response.text[:200]}"
            )
            assert response.headers["location"] == "/onboarding/"

    def test_a_role_denial_is_still_a_403(self, web_client_as):
        """**The other half, and the one that matters more.**

        Two unrelated conditions raise 403, and only one is recoverable. Redirecting a role
        denial would bounce a staff member who opened `/settings` into an onboarding wizard for
        an account they are already a member of — turning a correct refusal into what looks like
        a broken app, and quietly hiding that the app said no.
        """
        client = web_client_as("staff")
        response = client.get(
            "/settings", headers={"accept": "text/html"}, follow_redirects=False
        )
        assert response.status_code == 403, (
            f"a role denial answered {response.status_code} — the 403 handler is matching on the "
            f"status code rather than on the 'No account selected' detail"
        )

    def test_htmx_and_json_callers_keep_the_status_code(self, web_client_as):
        """Content negotiation, for the same reason the 401 handler does it.

        A 303 to an HTMX request swaps a wizard page into whatever fragment the request targeted,
        and a JSON client checking for 403 would follow the redirect and parse HTML as data.
        """
        client = web_client_as("owner")
        client.cookies.set(SESSION_COOKIE, _signed_in_without_account(web_client_as.connection))

        htmx = client.get(
            "/", headers={"accept": "text/html", "hx-request": "true"}, follow_redirects=False
        )
        assert htmx.status_code == 403

        api = client.get(
            "/", headers={"accept": "application/json"}, follow_redirects=False
        )
        assert api.status_code == 403

    def test_the_redirect_does_not_loop(self, web_client_as):
        """`/onboarding/` must not itself redirect, or the fix is an infinite loop.

        It is `Access.SESSION` so it does not raise this today; the handler excludes the prefix
        anyway, because a future non-SESSION route under it would fail far worse than a bare 403.
        """
        client = web_client_as("owner")
        client.cookies.set(SESSION_COOKIE, _signed_in_without_account(web_client_as.connection))

        landed = client.get("/", headers={"accept": "text/html"}, follow_redirects=True)
        assert landed.status_code == 200


def test_sign_in_binds_the_account_under_real_rls(_pg_engine):
    """**Sign-in could not find an existing member's account when RLS was enforced.**

    `sole_account_for` reads `memberships` to bind the session to the user's account. That is a
    tenant table, and these pre-account reads are covered not by `app.current_account` — there
    is no account yet, it is the thing being resolved — but by the `membership_self` policy,
    `USING (user_id = current_setting('app.current_user', true)::uuid)`.
    `auth/sessions.py`'s docstring names that policy as the enforcement for exactly these reads.

    But the GUC is only stamped by `tenancy/connection.py`'s `after_begin` listener from the
    `current_user` ContextVar, and at sign-in nothing has set it — the request is *becoming*
    authenticated. So the policy evaluated against NULL, zero memberships were visible, the
    session was never bound, and **every page answered `403 "No account selected"` for a user
    with a perfectly good membership.** Observed on a live install: the owner was sent to the
    onboarding wizard on every sign-in and the dashboard was unreachable.

    **Every other test in the suite connects as a superuser**, which bypasses RLS
    unconditionally (`rls.py` records that measurement) — so the policies were present and bound
    to nothing, and this was invisible. This test reaches the schema as the non-superuser
    `APP_ROLE`, and asserts RLS is actually on before proceeding so it cannot quietly stop
    exercising the policy.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session as _Session

    from tests.conftest import APP_PASSWORD, APP_ROLE

    name = f"mihomes_signin{uuid.uuid4().hex[:8]}"
    admin = create_engine(
        str(_pg_engine.url.set(database="postgres")), isolation_level="AUTOCOMMIT", future=True
    )
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')

    owner_url = str(_pg_engine.url.set(database=name))
    owner = create_engine(owner_url, future=True)
    account_id = uuid.uuid4()
    user_id = uuid.uuid4()
    try:
        from mihomes.models import Base

        Base.metadata.create_all(owner)

        with owner.begin() as conn:
            assert conn.execute(
                text(
                    "select relrowsecurity, relforcerowsecurity "
                    "from pg_class where relname = 'memberships'"
                )
            ).one() == (True, True), "RLS is not enforced here; the test proves nothing"

            conn.execute(
                text(
                    "insert into accounts (id, slug, name, type, plan, subscription_status) "
                    "values (:a, 'belle', 'Belle Estate', 'household', 'estate', 'active')"
                ),
                {"a": account_id},
            )
            conn.execute(
                text(
                    "insert into users (id, email, name, created_at) "
                    "values (:u, 'member@example.com', 'Member', now())"
                ),
                {"u": user_id},
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
            for stmt in (
                f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                f"TO {APP_ROLE}",
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
            ):
                conn.exec_driver_sql(stmt)
    finally:
        owner.dispose()

    app_engine = create_engine(
        make_url(owner_url).set(username=APP_ROLE, password=APP_PASSWORD), future=True
    )
    try:
        from mihomes.auth.session_flow import destination_for, sole_account_for

        with _Session(app_engine) as session:
            found = sole_account_for(session, user_id)
            assert found == account_id, (
                f"sign-in could not see this user's membership under RLS (got {found!r}) — the "
                f"session is left unbound and every page answers 403 'No account selected'"
            )
            assert destination_for(session, user_id) == "/", (
                "an existing member is sent to the onboarding wizard instead of the dashboard"
            )
    finally:
        app_engine.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{name}' AND pid <> pg_backend_pid()"
            )
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}"')
        admin.dispose()
