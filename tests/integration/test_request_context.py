"""G0 · pre-flight C12 — the request-scoped auth dependency.

**Why this module builds its own client instead of using `web_client_factory`.**
That fixture enters `account_context(account_a)` for the whole test
(`conftest.py:360`), so every web test today runs with a tenant already bound by
the *test harness* rather than by the application. That is precisely the crutch
that hid C12: `web/deps.py` defines only `get_db`, `lookup_session` has one call
site in `src/` (`routes/auth.py:191`, inside `signout_everywhere`), and
`account_context()` is entered nowhere in the web layer. A real deployed request
therefore reaches `require_account()` with an unset ContextVar and raises
`LookupError`.

So the client here is deliberately built **without** ambient context. If the
dependency does not bind the tenant, the probe route raises `LookupError` and the
test fails — which is the whole point. Binding it in the fixture would make this
test pass against an application that does nothing.

The probe route is mounted on the real app rather than asserting on the
dependency in isolation, because the failure mode being guarded is a *transport*
one: FastAPI runs sync endpoints in a threadpool, and a ContextVar set in the
wrong place propagates to the handler on some paths and not others. Only an
end-to-end request proves the value is visible where queries actually run.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from mihomes.auth.sessions import SESSION_COOKIE, hash_session_id
from mihomes.tenancy import require_account, require_user


def _make_user(conn, *, email: str = "owner@example.com") -> uuid.UUID:
    """Insert a GLOBAL user with raw SQL.

    Raw SQL for the same reason `conftest._make_account` uses it: these rows are
    the input to the tenant machinery under test, so creating them must not
    depend on that machinery being wired correctly.
    """
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, google_sub, email, name, created_at) "
            "VALUES (:id, :sub, :email, 'Test Owner', now())"
        ),
        {"id": user_id, "sub": f"sub-{user_id.hex[:12]}", "email": email},
    )
    return user_id


def _make_membership(
    conn, *, account_id: uuid.UUID, user_id: uuid.UUID, role: str = "owner",
    status: str = "active",
) -> uuid.UUID:
    membership_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO memberships (id, account_id, user_id, role, status, created_at) "
            "VALUES (:id, :account_id, :user_id, :role, :status, now())"
        ),
        {
            "id": membership_id,
            "account_id": account_id,
            "user_id": user_id,
            "role": role,
            "status": status,
        },
    )
    return membership_id


def _make_session_row(
    conn, *, user_id: uuid.UUID, account_id: uuid.UUID | None
) -> str:
    """Create a session row and return the RAW cookie value.

    Only the hash is stored (SPEC-002 G12), so the raw value has to be minted
    here and handed to the client — exactly as `create_session` does in
    production.
    """
    raw = f"raw-session-{uuid.uuid4().hex}"
    conn.execute(
        text(
            "INSERT INTO sessions "
            "(id, session_id_hash, user_id, current_account_id, created_at, expires_at) "
            "VALUES (:id, :hash, :user_id, :account_id, now(), :expires)"
        ),
        {
            "id": uuid.uuid4(),
            "hash": hash_session_id(raw),
            "user_id": user_id,
            "account_id": account_id,
            "expires": datetime.now(timezone.utc) + timedelta(days=1),
        },
    )
    return raw


@pytest.fixture
def unbound_client(_pg_engine, account_a):
    """A TestClient with **no** ambient account context.

    Yields `(make, connection)` where `make()` returns the client and the
    connection is exposed so a test can seed rows inside the same transaction.
    """
    from fastapi.testclient import TestClient

    from mihomes.web.app import create_app
    from mihomes.web.deps import get_db

    stack = contextlib.ExitStack()
    connection = stack.enter_context(_pg_engine.connect())
    transaction = connection.begin()
    stack.callback(transaction.rollback)

    SessionLocal = sessionmaker(
        bind=connection, future=True, join_transaction_mode="create_savepoint"
    )

    def override_get_db():
        s = SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def make():
        from mihomes.web.deps import require_authenticated

        app = create_app()

        # The probe reads the ContextVars the scoped session actually consults.
        # `require_account()` raises LookupError when unset, so an application
        # that never binds the tenant fails here rather than returning wrong data.
        @app.get("/__probe__/context")
        def _probe(auth=require_authenticated()):
            return {
                "account_id": str(require_account()),
                "user_id": str(require_user()),
            }

        app.dependency_overrides[get_db] = override_get_db
        return stack.enter_context(
            TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        )

    try:
        yield make, connection
    finally:
        stack.close()


def test_request_binds_account_context(unbound_client, account_a):
    """An authenticated request binds `current_account` and `current_user`.

    This is the C12 gate. Without a dependency that enters `account_context`,
    the probe raises `LookupError` — the same failure a deployed request hits on
    every tenant query today.
    """
    make, connection = unbound_client
    user_id = _make_user(connection)
    _make_membership(connection, account_id=account_a, user_id=user_id)
    raw = _make_session_row(connection, user_id=user_id, account_id=account_a)

    client = make()
    response = client.get("/__probe__/context", cookies={SESSION_COOKIE: raw})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account_id"] == str(account_a)
    assert body["user_id"] == str(user_id)


def test_revoked_membership_not_resolved(unbound_client, account_a):
    """D8/N10 — revocation takes effect on the very next request.

    The session row is untouched and still names the account; only the
    membership's status changed. The request must not resolve, and must not
    reach the route. Asserting on the *status code* rather than on
    `lookup_session` directly is what makes this a test of the dependency
    (which is new) rather than of `lookup_session` (which SPEC-002 already
    covers).
    """
    make, connection = unbound_client
    user_id = _make_user(connection, email="revoked@example.com")
    _make_membership(
        connection, account_id=account_a, user_id=user_id, status="revoked"
    )
    raw = _make_session_row(connection, user_id=user_id, account_id=account_a)

    client = make()
    response = client.get("/__probe__/context", cookies={SESSION_COOKIE: raw})

    assert response.status_code != 200, (
        "a revoked membership must not authorise a request — the session row "
        "survives but authorises nothing (SPEC-002 A17, SPEC-003 D8)"
    )


def test_unauthenticated_request_is_refused(unbound_client):
    """No cookie at all → refused, and the tenant is never bound.

    The fail-closed direction: an unbound ContextVar must not be reachable by an
    anonymous caller, because `require_account()` raising `LookupError` inside a
    route would surface as a 500 rather than an authentication failure.
    """
    make, _connection = unbound_client
    client = make()
    response = client.get("/__probe__/context")

    assert response.status_code != 200
    assert response.status_code != 500, (
        "an anonymous request must be refused by the dependency, not crash "
        "inside the route on an unset ContextVar"
    )
