"""Fixtures for SPEC-003's request-level tests.

**Why these live here rather than in the root `conftest.py`.** The root's `web_client_factory`
enters `account_context(account_a)` for the whole test (`conftest.py:360`), which is exactly the
crutch that hid pre-flight C12: the entire web suite passed against an application that never
bound a tenant to a request. These fixtures deliberately build a client **without** ambient
context, so the application's own `require_authenticated` has to do the binding. Putting them
beside the root fixture would invite someone to "simplify" one into the other.

Scoped to `tests/integration/` because only request-level tests need them; the unit tests for the
authz primitives take plain sessions.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from mihomes.auth.sessions import hash_session_id


def _insert_user(conn, *, email: str) -> uuid.UUID:
    """Insert a GLOBAL user with raw SQL.

    Raw SQL for the same reason the root conftest's `_make_account` uses it: these rows are the
    *input* to the tenant machinery under test, so creating them must not depend on that
    machinery already working.
    """
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, google_sub, email, name, created_at) "
            "VALUES (:id, :sub, :email, 'Test User', now())"
        ),
        {"id": user_id, "sub": f"sub-{user_id.hex[:12]}", "email": email},
    )
    return user_id


def _insert_membership(
    conn, *, account_id, user_id, role: str = "owner", status: str = "active"
) -> uuid.UUID:
    membership_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO memberships (id, account_id, user_id, role, status, created_at) "
            "VALUES (:id, :account_id, :user_id, :role, :status, now())"
        ),
        {
            "id": membership_id, "account_id": account_id, "user_id": user_id,
            "role": role, "status": status,
        },
    )
    return membership_id


def _insert_session(conn, *, user_id, account_id) -> str:
    """Create a session row and return the RAW cookie value.

    Only the hash is stored (SPEC-002 G12), so the raw value has to be minted here and handed to
    the client — exactly as `create_session` does in production.
    """
    raw = f"raw-session-{uuid.uuid4().hex}"
    conn.execute(
        text(
            "INSERT INTO sessions "
            "(id, session_id_hash, user_id, current_account_id, created_at, expires_at) "
            "VALUES (:id, :hash, :user_id, :account_id, now(), :expires)"
        ),
        {
            "id": uuid.uuid4(), "hash": hash_session_id(raw), "user_id": user_id,
            "account_id": account_id,
            "expires": datetime.now(timezone.utc) + timedelta(days=1),
        },
    )
    return raw


@pytest.fixture
def auth_seed():
    """`auth_seed(connection, account_id, role=...) -> (raw_cookie, user_id)`.

    One call to get a signed-in member of an account, so a test that is *about* authorization
    does not spend twenty lines constructing an identity.
    """

    def _seed(conn, account_id, *, role: str = "owner", status: str = "active"):
        user_id = _insert_user(conn, email=f"{role}-{uuid.uuid4().hex[:6]}@example.com")
        _insert_membership(
            conn, account_id=account_id, user_id=user_id, role=role, status=status
        )
        raw = _insert_session(conn, user_id=user_id, account_id=account_id)
        return raw, user_id

    return _seed


@pytest.fixture
def web_client_as(_pg_engine, account_a):
    """`web_client_as(role="staff", scoped_to=[prop]) -> TestClient` — the real app, signed in.

    The §9 manifest asks for `owner_a` / `admin_a` / `staff_a` (scoped to one property) and
    `staff_a_unscoped` (zero scope rows — the fail-closed case). A factory rather than four
    fixtures, because the interesting tests need *two* clients in one test to compare what each
    role sees, and fixtures cannot be parameterised per use.

    Deliberately **no ambient `account_context`**: the application binds the tenant itself via
    `enforce_declared_action`. Seeding rows is done on the raw connection for the same reason.
    """
    from fastapi.testclient import TestClient

    from mihomes.auth.sessions import SESSION_COOKIE
    from mihomes.tenancy import account_context
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

    def seed(fn):
        """Run `fn(session)` inside the account context, for building fixtures data."""
        with account_context(account_a), SessionLocal() as s:
            fn(s)
            s.commit()

    def make(role: str = "owner", scoped_to=()):
        user_id = _insert_user(connection, email=f"{role}-{uuid.uuid4().hex[:6]}@example.com")
        membership_id = _insert_membership(
            connection, account_id=account_a, user_id=user_id, role=role
        )
        for prop_id in scoped_to:
            connection.execute(
                text(
                    "INSERT INTO membership_property_scopes "
                    "(id, account_id, membership_id, property_id, created_at) "
                    "VALUES (:id, :a, :m, :p, now())"
                ),
                {"id": uuid.uuid4(), "a": account_a, "m": membership_id, "p": prop_id},
            )
        raw = _insert_session(connection, user_id=user_id, account_id=account_a)

        app = create_app()
        app.dependency_overrides[get_db] = override_get_db
        client = stack.enter_context(
            TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        )
        client.cookies.set(SESSION_COOKIE, raw)
        return client

    def session_for_scope(scoped_to=()):
        """A plain ORM session on the test's connection, for exercising service-layer code.

        The AI executors take a `Session` directly rather than going through HTTP, so testing
        them means calling them the way `web/routes/ai.py` does — with the authz context bound by
        the caller. `scoped_to` is accepted for symmetry and to document intent at the call site;
        the scope itself is bound by the test via `authz_context`, because that is the seam the
        production code reads.

        The **account** context is bound here rather than by the test, because it is Phase 1's
        concern and not what these tests are about: without it every `TenantOwned` query raises
        `LookupError`, and the test would be measuring the tenant layer instead of the property
        one.
        """
        stack.enter_context(account_context(account_a))
        return stack.enter_context(SessionLocal())

    make.seed = seed
    make.connection = connection
    make.session_for_scope = session_for_scope
    try:
        yield make
    finally:
        stack.close()
        # **Deny-audit rows outlive this fixture's rollback, by design — so clean them here.**
        #
        # `authz/audit.py audit_deny` writes on an *independent* session and commits, because a
        # deny audit written through the request session is discarded by the very rollback that
        # reports the denial (A33). Correct behaviour, and it means every route test that provokes
        # a 403 leaves a committed `audit_log` row behind that this fixture's `transaction.rollback`
        # cannot reach.
        #
        # It is not hypothetical and it is not local. `test_leak_matrix.py` provokes two denials,
        # and those rows were still visible ~380 tests later to
        # `test_archive.py::TestGetStats::test_counts_eligible_rows`, which counted 3 where it
        # seeded 2 — a failure that looks like a bug in the archive service and is not. The rows
        # belong to *other* accounts, and would be invisible if `session.query(M).count()` honoured
        # the tenant filter; it does not (see `authz/query_scope.py`'s note on `all_mappers`), and
        # RLS does not apply on the superuser connection the suite runs on. So the pollution and
        # the count gap only bite when combined, which is what made it look intermittent.
        #
        # Deleting by account keeps this narrow: only rows this fixture's own tenant produced.
        # `tests/integration/test_audit.py::audit_factory` does the same thing for the same reason.
        with _pg_engine.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM audit_log WHERE account_id = :account_id"),
                {"account_id": account_a},
            )


@pytest.fixture
def unbound_client(_pg_engine, account_a):
    """A `TestClient` with **no** ambient account context.

    Yields `(make, connection)`. `make()` returns the client; the connection is exposed so a test
    can seed rows inside the same transaction the app will read through.

    The probe route reads the ContextVars the scoped session actually consults, so an application
    that never binds the tenant fails here rather than returning wrong data.
    """
    from fastapi.testclient import TestClient

    from mihomes.tenancy import require_account, require_user
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

        @app.get("/__probe__/context")
        def _probe(auth=require_authenticated()):
            return {
                "account_id": str(require_account()),
                "user_id": str(require_user()),
                "role": auth.role,
            }

        app.dependency_overrides[get_db] = override_get_db
        return stack.enter_context(
            TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        )

    try:
        yield make, connection
    finally:
        stack.close()
