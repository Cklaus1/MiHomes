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
