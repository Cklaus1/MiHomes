"""Shared test fixtures — Postgres-backed and account-scoped (SPEC-002 Step 15, A23).

**The `session` fixture keeps its name and its semantics.** 38 test files take it
(measured; the spec says 28 of 33, written before Phase 0). It still yields a
`Session` that rolls back after the test — what changed is that the rows it creates
now belong to an account, because `TenantOwned` made `account_id` NOT NULL on 40
tables. Renaming it would have meant touching 38 files to no benefit.

Two databases are in play and they are deliberately separate:

  mihomes_phase0   owned by the `alembic_landing/` tree — SPEC-001's waitlist tests
  mihomes_test     this suite (TEST_DATABASE_URL)

Schema is created with `Base.metadata.create_all()`, not by running migrations.
That is the right call *for tests*: the baseline migration is G6.2's deliverable and
has its own round-trip gate, so making every test depend on it would couple the
whole suite to one artifact and make a migration bug look like 900 unrelated
failures.

**Skipping when `TEST_DATABASE_URL` is unset is a RED gate, not a pass**
(build-loop-conventions §0). CI always sets it; a local run without it is telling
you the suite did not really run.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker

from mihomes.models import Base
from mihomes.models.staff import StaffRole
from mihomes.services import property as prop_svc
from mihomes.services import space as space_svc
from mihomes.services import staff as staff_svc
from mihomes.services import vendor as vendor_svc

# Imports `mihomes.tenancy`, whose __init__ installs the before_flush listener that
# stamps account_id on insert (G8.3). Without it every insert here fails NOT NULL.
from mihomes.tenancy import account_context

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# For test MODULES that want to declare the dependency explicitly via
# `pytestmark = needs_postgres`. It cannot decorate a fixture — pytest rejects marks
# on fixtures ("Marks applied to fixtures have no effect", PytestRemovedIn9Warning).
# The fixtures below get their skip from `_pg_engine`, which every one of them
# depends on and which skips internally.
needs_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason=(
        "TEST_DATABASE_URL unset — SPEC-002 Step 15 makes this suite Postgres-only. "
        "A skip here means the tenancy criteria did NOT run (conventions §0)."
    ),
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    # Bound to the Engine *class*, so this fires for every engine in the test
    # session — including Postgres, where PRAGMA is a syntax error. Check the driver
    # on the raw connection: there is no engine in scope to ask for a dialect.
    #
    # Kept even though the shared fixtures are now Postgres: tests/web/conftest.py
    # and a few migration tests still build SQLite engines on purpose.
    if type(dbapi_conn).__module__.split(".")[0] != "sqlite3":
        return
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture(scope="session")
def _pg_engine():
    """One engine for the whole test session; schema built once.

    Session-scoped because `create_all` over 44 tables per test would dominate the
    runtime. Isolation between tests comes from the per-test transaction rollback in
    `session`, not from rebuilding the schema.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL unset")

    engine = create_engine(TEST_DATABASE_URL, future=True, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def engine(_pg_engine):
    """Kept for the handful of tests that ask for an engine rather than a session."""
    return _pg_engine


def _make_account(conn, *, slug: str, name: str) -> uuid.UUID:
    """Insert an account with raw SQL.

    Deliberately not via the ORM: the ORM path will soon be tenant-scoped (G8), and
    creating the very first account cannot itself require an account context. Raw
    SQL keeps the fixture independent of the machinery it exists to test.
    """
    account_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO accounts (id, slug, name, type, plan, created_at, updated_at) "
            "VALUES (:id, :slug, :name, 'household', 'free', now(), now())"
        ),
        {"id": account_id, "slug": slug, "name": name},
    )
    return account_id


def _create_account(engine, *, prefix: str, name: str) -> uuid.UUID:
    """Create and COMMIT an account, then close the connection.

    The commit has to complete *before* the fixture yields. Yielding from inside
    `with engine.begin()` keeps the transaction open for the whole test, and the
    `session` fixture runs on a different connection — so it could not see the row
    and every insert failed `properties_account_id_fkey`. The row was there; it just
    was not committed yet.
    """
    with engine.begin() as conn:
        return _make_account(
            conn, slug=f"{prefix}-{uuid.uuid4().hex[:8]}", name=name
        )


@pytest.fixture
def account_a(_pg_engine):
    """The account almost every test operates inside."""
    return _create_account(_pg_engine, prefix="acct-a", name="Account A")


@pytest.fixture
def account_b(_pg_engine):
    """A *second* account, for the tests that matter most.

    A21 needs two tenants to prove isolation, and a single-account fixture cannot
    express "A must not see B" — the assertion Phase 1's definition of done rests on.
    """
    return _create_account(_pg_engine, prefix="acct-b", name="Account B")


@pytest.fixture
def session(_pg_engine, account_a):
    """Account-scoped session that rolls back after each test.

    Same name and same rollback semantics as before, now with a bound tenant. The
    rollback is what keeps tests independent: each runs inside a transaction that is
    discarded, so the session-scoped schema is never mutated across tests.
    """
    connection = _pg_engine.connect()
    transaction = connection.begin()
    # join_transaction_mode="create_savepoint" so a test's own commit()/rollback()
    # behaves as it expects while still living inside the outer transaction.
    #
    # Without it, a test that commits and later rolls back (test_real_data's
    # idempotency check does exactly that, deliberately) loses its committed rows —
    # the rollback unwound past the commit and the count came back 0 instead of 4.
    # The savepoint gives the session a nested scope to roll back to, and the outer
    # transaction still discards everything at teardown so tests stay independent.
    Session = sessionmaker(
        bind=connection, future=True, join_transaction_mode="create_savepoint"
    )
    sess = Session()

    with account_context(account_a):
        try:
            yield sess
        finally:
            sess.close()
            # Roll back the OUTER transaction: anything the test committed is inside
            # it and goes with it, so the session-scoped schema is never mutated.
            transaction.rollback()
            connection.close()


APP_ROLE = "mihomes_test_app"
APP_PASSWORD = "mihomes_test_app"


@pytest.fixture(scope="session")
def app_engine(_pg_engine):
    """An engine connected as a **non-superuser** role, so RLS actually applies.

    **Why this fixture has to exist.** `_pg_engine` connects as `postgres`, a superuser, and
    **superusers bypass RLS unconditionally — even with `FORCE ROW LEVEL SECURITY`.**
    Measured: as `postgres` with the GUC unset, a FORCE-protected table returned every row.
    So every test in this suite runs with RLS inert, and RLS could be entirely broken
    without a single failure. That is the largest false-green surface in SPEC-002, and it is
    structural rather than an oversight — nothing about a passing test tells you which role
    ran it.

    **Any test that means to prove tenant isolation must take this fixture, A21 included.**
    A21 run as `postgres` demonstrates that the G8 ORM filter works while reporting that
    RLS does. `test_rls.py::test_app_role_is_not_a_superuser` fails loudly if this engine
    ever points at a superuser, because a later edit that quietly repointed it would turn
    the whole RLS suite green and meaningless.

    The role is created here rather than in `0002_rls`: a role is cluster-wide, not
    per-database, so `CREATE ROLE` in a migration collides the second time it runs against
    another database in the same cluster. The migration's docstring carries the production
    equivalent.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL unset")

    with _pg_engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.exec_driver_sql(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                    CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}';
                END IF;
            END $$;
            """
        )
        # USAGE on the schema is required and is NOT implied by the table grants: without
        # it every table reports "relation does not exist" rather than "permission denied".
        conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        conn.exec_driver_sql(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
            f"TO {APP_ROLE}"
        )
        conn.commit()

    url = make_url(TEST_DATABASE_URL).set(username=APP_ROLE, password=APP_PASSWORD)
    engine = create_engine(url, future=True, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def seed_estate():
    """The minimal estate two web suites both want: one property, one room, two
    people (staff + resident), one vendor.

    Exposed as a fixture rather than a plain helper module because `tests/` has no
    `__init__.py`, so `tests/web/` and `tests/integration/` cannot import a shared
    module from `tests/` — but they can both see root-conftest fixtures.
    """

    def _seed(s):
        prop = prop_svc.create_property(s, "Test Manor")
        space_svc.create_space(s, "Living Room", prop.slug)
        staff_svc.create_staff(
            s, "Marcia Staff", role=StaffRole.HOUSEKEEPER,
            property_id_or_slug=prop.slug,
        )
        staff_svc.create_staff(s, "Rita Resident", role=StaffRole.RESIDENT)
        vendor_svc.create_vendor(s, "Acme Pest", service_categories=["Pest Control"])

    return _seed


@pytest.fixture
def web_client_factory(_pg_engine, account_a):
    """Build account-scoped FastAPI `TestClient`s over Postgres.

    Four places used to hand-roll this fixture against in-memory SQLite. G2 made
    `account_id` NOT NULL on 40 tables and Step 15 moved the suite to Postgres, so all
    four broke the same way at once (`LookupError: current_account` — fail-closed
    working as designed). Rather than repair the same body four times, they now share
    this one. That duplication is what bit SPEC-001's `_unmanaged` sets too: a fix
    applied to the shared copy silently misses the local overrides.

    The account context stays open for the whole test, not just for requests, because
    several tests write through `client._SessionLocal()` directly and those inserts
    need the G8.3 stamp listener to find a tenant.
    """
    # Imported lazily: this conftest is loaded for every test in the suite, and only
    # web tests need FastAPI.
    from fastapi.testclient import TestClient

    from mihomes.web.app import create_app
    from mihomes.web.deps import get_db

    stack = contextlib.ExitStack()

    def make(seed=None, *, raise_server_exceptions=True):
        connection = stack.enter_context(_pg_engine.connect())
        transaction = connection.begin()
        # Registered as a callback so it runs during unwinding even if the test body
        # raises. The old per-file fixtures put `transaction.rollback()` after the
        # `with` block, where an exception thrown into the generator leaked the
        # connection and left the outer transaction open.
        stack.callback(transaction.rollback)
        SessionLocal = sessionmaker(
            bind=connection, future=True, join_transaction_mode="create_savepoint"
        )
        stack.enter_context(account_context(account_a))

        if seed is not None:
            with SessionLocal() as s:
                seed(s)
                s.commit()

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

        app = create_app()
        app.dependency_overrides[get_db] = override_get_db
        # Loopback base_url so the H30 Host guard accepts requests by default; the
        # foreign-host test overrides the Host header explicitly.
        client = stack.enter_context(
            TestClient(
                app,
                base_url="http://localhost",
                raise_server_exceptions=raise_server_exceptions,
            )
        )
        client._SessionLocal = SessionLocal
        return client

    try:
        yield make
    finally:
        # LIFO: TestClient closed, account context exited, transaction rolled back,
        # connection returned. The rollback is what keeps the session-scoped schema
        # clean between tests, seed rows included.
        stack.close()


@pytest.fixture
def session_b(_pg_engine, account_b):
    """A session bound to the *other* account. Pairs with `session` for A21."""
    connection = _pg_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(
        bind=connection, future=True, join_transaction_mode="create_savepoint"
    )
    sess = Session()

    with account_context(account_b):
        try:
            yield sess
        finally:
            sess.close()
            transaction.rollback()
            connection.close()
