"""G9 · §6 Step 9 — the tenant GUC on a pooled connection (A11, N3).

**`pool_size=1, max_overflow=0` in these fixtures is the whole experiment.** Every
transaction is forced onto the *same physical connection*, which is the only condition under
which a leak is observable. With a default pool the second transaction may get a fresh
connection and the test passes while the bug is intact — so a version of this file without the
pool pinning would be the exact false-green N3 warns about.

Read as a pair with `tenancy/connection.py`, which records the measurements these tests came
from: a session-level GUC survives both the transaction *and* the pool checkin, and a
transaction-local one reads back as `''` afterwards rather than NULL.
"""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from mihomes.models.property import Property
from mihomes.tenancy import account_context
from mihomes.tenancy.connection import ACCOUNT_GUC, USER_GUC

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("TEST_DATABASE_URL"),
    reason="needs Postgres; a skip here means A11 was NOT verified",
)


@pytest.fixture
def single_conn_engine():
    """One connection, no overflow — so reuse across tenants is guaranteed, not hoped for."""
    import os

    engine = create_engine(
        os.environ["TEST_DATABASE_URL"],
        future=True,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    yield engine
    engine.dispose()


def _read_guc(conn, guc: str):
    return conn.execute(text("SELECT current_setting(:g, true)"), {"g": guc}).scalar()


# --- A11: the gate ------------------------------------------------------------------

def test_no_guc_leak_across_transactions(single_conn_engine, account_a, account_b):
    """A11 — two sequential transactions on one pooled connection, different accounts.

    The spec's clause: they must never see each other's rows. Checked at the GUC level here
    (the mechanism) and at the row level in `test_rows_do_not_leak_across_pooled_transactions`
    (the consequence), because a correct GUC with a broken policy and a broken GUC with a
    correct policy fail differently.
    """
    Session = sessionmaker(bind=single_conn_engine, future=True)

    seen = []
    for account in (account_a, account_b, account_a):
        with account_context(account):
            with Session() as s:
                # after_begin fires on the first statement of the transaction.
                seen.append(uuid.UUID(_read_guc(s.connection(), ACCOUNT_GUC)))

    assert seen == [account_a, account_b, account_a], (
        f"GUC did not follow the context across pooled transactions: {seen}"
    )


def test_guc_is_transaction_local_not_session_scoped(single_conn_engine, account_a):
    """N3 — the GUC must not outlive its transaction.

    A session-scoped `SET` survives the transaction *and* the pool checkin (both measured;
    see `tenancy/connection.py`). This asserts ours does not: after the account-bound
    transaction ends, a bare transaction on the same connection must not still be stamped.

    `''` is the expected residue rather than `None` — Postgres leaves an emptied
    transaction-local GUC readable as an empty string, which is precisely why
    `tenancy/rls.py`'s predicate wraps it in `NULLIF`.
    """
    Session = sessionmaker(bind=single_conn_engine, future=True)

    with account_context(account_a):
        with Session() as s:
            assert uuid.UUID(_read_guc(s.connection(), ACCOUNT_GUC)) == account_a

    # No context now. Same physical connection.
    with Session() as s:
        residue = _read_guc(s.connection(), ACCOUNT_GUC)
        assert residue in (None, ""), (
            f"the GUC outlived its transaction: {residue!r} — it was set session-scoped, "
            "which is exactly what N3 forbids"
        )


def test_rows_do_not_leak_across_pooled_transactions(
    single_conn_engine, _pg_engine, app_engine, account_a, account_b
):
    """The consequence N3 is about, checked on a **non-superuser** connection.

    This has to use `app_engine`'s role: as `postgres` the rows would be visible regardless of
    the GUC, because superusers bypass RLS (G7). So the pool is pinned *and* the role is
    non-superuser — either one alone makes this test unable to fail.
    """
    import os

    from sqlalchemy.engine import make_url

    from tests.conftest import APP_PASSWORD, APP_ROLE

    url = make_url(os.environ["TEST_DATABASE_URL"]).set(
        username=APP_ROLE, password=APP_PASSWORD
    )
    engine = create_engine(url, future=True, pool_size=1, max_overflow=0)
    Session = sessionmaker(bind=engine, future=True)

    suffix = uuid.uuid4().hex[:8]
    with _pg_engine.begin() as conn:
        for account, slug in ((account_a, f"pool-a-{suffix}"), (account_b, f"pool-b-{suffix}")):
            conn.execute(
                text(
                    "INSERT INTO properties (id, account_id, name, slug, property_type, "
                    "status, currency, occupied) "
                    "VALUES (:id, :acct, :slug, :slug, 'PRIMARY', 'OPEN', 'USD', false)"
                ),
                {"id": uuid.uuid4(), "acct": account, "slug": slug},
            )
    try:
        for account, mine, theirs in (
            (account_a, f"pool-a-{suffix}", f"pool-b-{suffix}"),
            (account_b, f"pool-b-{suffix}", f"pool-a-{suffix}"),
            (account_a, f"pool-a-{suffix}", f"pool-b-{suffix}"),
        ):
            with account_context(account):
                with Session() as s:
                    slugs = {p.slug for p in s.query(Property).all()}
                    assert mine in slugs, f"{account} could not see its own row"
                    assert theirs not in slugs, (
                        f"{account} saw {theirs!r} on a reused pooled connection"
                    )
    finally:
        engine.dispose()
        with _pg_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM properties WHERE slug LIKE :p"), {"p": f"pool-%-{suffix}"}
            )


# --- both GUCs, not just the account ----------------------------------------------

def test_user_guc_is_set_too(single_conn_engine, account_a):
    """§4.4 sets only `app.current_account`; `membership_self` needs `app.current_user`.

    A10's bootstrap policy is keyed on the user GUC, so setting only the account leaves it
    permanently unsatisfiable and the account picker returns an empty list — the failure would
    surface as "sign-in works but you belong to no accounts".
    """
    Session = sessionmaker(bind=single_conn_engine, future=True)
    user_id = uuid.uuid4()
    with account_context(account_a, user_id=user_id):
        with Session() as s:
            conn = s.connection()
            assert uuid.UUID(_read_guc(conn, ACCOUNT_GUC)) == account_a
            assert uuid.UUID(_read_guc(conn, USER_GUC)) == user_id


def test_membership_self_works_through_the_real_app_path(_pg_engine, account_a):
    """A10, end to end — the claim G7 deliberately left open.

    G7 built `membership_self` and verified the *policy* by setting `app.current_user` by hand,
    recording that A10 was not truly satisfied until G9 wired the GUC, because otherwise the
    account picker returns an empty list. This closes that: the user is bound via
    `account_context(..., user_id=...)`, the GUC is set by `after_begin`, and the read goes
    through a **non-superuser** connection so RLS is actually in force.

    Deliberately queries with **no account bound** — that is the pre-picker state the policy
    exists for, and the account GUC is stamped NULL.
    """
    import os

    from sqlalchemy.engine import make_url

    from tests.conftest import APP_PASSWORD, APP_ROLE

    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    with _pg_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, google_sub, email) VALUES (:id, :s, :e)"),
            {"id": user_id, "s": f"sub-{suffix}", "e": f"{suffix}@example.com"},
        )
        conn.execute(
            text(
                "INSERT INTO memberships (id, account_id, user_id, role, status) "
                "VALUES (:id, :a, :u, 'OWNER', 'ACTIVE')"
            ),
            {"id": membership_id, "a": account_a, "u": user_id},
        )

    url = make_url(os.environ["TEST_DATABASE_URL"]).set(
        username=APP_ROLE, password=APP_PASSWORD
    )
    engine = create_engine(url, future=True, pool_size=1, max_overflow=0)
    Session = sessionmaker(bind=engine, future=True)
    try:
        # A user, but NO account — exactly what the picker has to work with.
        from mihomes.tenancy.context import current_user

        token = current_user.set(user_id)
        try:
            with Session() as s:
                rows = s.execute(
                    text("SELECT account_id FROM memberships")
                ).fetchall()
        finally:
            current_user.reset(token)

        assert account_a in {r[0] for r in rows}, (
            "membership_self did not expose the user's own membership through the real "
            "app path — the account picker would return an empty list"
        )
    finally:
        engine.dispose()
        with _pg_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM memberships WHERE id = :i"), {"i": membership_id}
            )
            conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})


def test_no_context_leaves_both_gucs_unset(single_conn_engine):
    """A transaction with no tenant is legitimate and must not raise here.

    Sign-in reading GLOBAL `users`, the account picker before a choice, Alembic. `after_begin`
    leaves the GUC unset so RLS returns zero rows — fail closed. Raising instead would break
    those paths for nothing, and the read filter in `tenancy/session.py` already refuses
    tenant-table access without a context.
    """
    Session = sessionmaker(bind=single_conn_engine, future=True)
    with Session() as s:
        conn = s.connection()
        assert _read_guc(conn, ACCOUNT_GUC) in (None, "")
        assert _read_guc(conn, USER_GUC) in (None, "")


# --- pool checkin ------------------------------------------------------------------

def test_a_leaked_session_guc_cannot_be_observed_by_a_scoped_transaction(
    single_conn_engine, account_a
):
    """Step 9 asks for a pool `checkin` `RESET`; this is what replaced it, and why.

    The checkin RESET does not work: executing SQL in `checkin` leaves an implicit transaction
    open, and SQLAlchemy's own reset (which restores the isolation level, i.e. sets
    `autocommit`) then fails with `can't change 'autocommit' now: connection in transaction
    status INERROR`. Measured — it broke every fixture sharing the pool. `RESET` is also itself
    transactional, so one issued inside a transaction that later rolls back is undone.

    Stamping every transaction is stronger regardless. A session-level GUC *does* survive both
    the transaction and the pool checkin (measured), so this deliberately plants one and then
    asserts that a subsequent transaction cannot see it — because `after_begin` overrides it
    transaction-locally, with `NULL` when nothing is bound.
    """
    Session = sessionmaker(bind=single_conn_engine, future=True)

    # Plant a session-scoped value — the thing N3 forbids — on the pool's only connection.
    with single_conn_engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text("SELECT set_config(:g, 'leaked', false)"), {"g": ACCOUNT_GUC}
            )
        conn.rollback()
        assert _read_guc(conn, ACCOUNT_GUC) == "leaked", (
            "precondition: a session-scoped SET should survive its transaction"
        )

    # Same physical connection (pool_size=1). Bound context wins.
    with account_context(account_a):
        with Session() as s:
            assert uuid.UUID(_read_guc(s.connection(), ACCOUNT_GUC)) == account_a

    # And with no context, the leaked value must still not be visible.
    with Session() as s:
        observed = _read_guc(s.connection(), ACCOUNT_GUC)
        assert observed in (None, ""), (
            f"a leaked session-scoped GUC was observable as {observed!r} — after_begin is not "
            "stamping NULL when no account is bound"
        )
