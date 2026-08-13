"""G8 · §6 Step 8 — the session-level tenant filter (A5, A6, A7).

The ORM half of the guarantee. RLS (G7) is the database half, and the two are
independent on purpose: RLS does not see raw `text()` at all from the ORM's side, and this
filter does not reach the two Core association tables. Neither is a backstop for the other
in every case, which is why both exist.

Almost everything here needs **two** accounts to mean anything: a single-account fixture would
pass against a filter that did nothing at all. `test_bulk_ops_scoped` is the one with the most
at stake — N2's case, where a missing guard lets one tenant rewrite or delete every other
tenant's rows in a single statement.
"""


import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from mihomes.models.property import Property, PropertyType
from mihomes.models.task import Task
from mihomes.tenancy import account_context
from mihomes.tenancy.session import SKIP_TENANT


def _property(session, slug: str) -> Property:
    p = Property(name=slug.title(), slug=slug, property_type=PropertyType.PRIMARY)
    session.add(p)
    session.flush()
    return p


# --- A7: inserts are stamped -------------------------------------------------------

def test_insert_stamped(session, account_a):
    """The listener supplies `account_id` so the caller never has to."""
    p = _property(session, "stamped")
    assert p.account_id == account_a


def test_explicit_account_id_is_respected(session, account_a, account_b):
    """Only `None` is filled in.

    The importer (Step 16) and admin tooling write rows *for* a tenant while running under
    another context, so an explicit account_id must win over the ambient one. Note the row is
    then invisible to this session's own reads, which is the filter working, not a bug.
    """
    p = Property(
        name="Explicit", slug="explicit", property_type=PropertyType.PRIMARY,
        account_id=account_b,
    )
    session.add(p)
    session.flush()
    assert p.account_id == account_b


# --- A5: reads are scoped ----------------------------------------------------------

def test_reads_are_scoped_to_the_current_account(_pg_engine, account_a, account_b):
    """The core of A5: two accounts, one query shape, no bleed."""
    Session = sessionmaker(bind=_pg_engine, future=True)

    for account, slug in ((account_a, "a-house"), (account_b, "b-house")):
        with account_context(account):
            with Session() as s:
                _property(s, slug)
                s.commit()

    try:
        for account, mine, theirs in (
            (account_a, "a-house", "b-house"),
            (account_b, "b-house", "a-house"),
        ):
            with account_context(account):
                with Session() as s:
                    slugs = {p.slug for p in s.query(Property).all()}
                    assert mine in slugs
                    assert theirs not in slugs, (
                        f"account {account} read {theirs!r} — the filter did not apply"
                    )
    finally:
        # Committed rows in a session-scoped schema must be cleaned up explicitly; see the
        # `committed` fixture in test_rls.py for what leaking them costs.
        with _pg_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM properties WHERE slug IN ('a-house','b-house')")
            )


def test_filter_is_not_cached_across_accounts(_pg_engine, account_a, account_b):
    """One query shape, two accounts, in sequence — the shape that catches a stale bound value.

    SQLAlchemy compiles criteria lambdas and caches them, tracking **closure variables** so
    they become per-execution bound parameters. `account_id` is read into a local precisely so
    it is such a variable.

    What actually happens if you inline `current_account.get()` into the lambda, as §4.4 does,
    is **not** silent caching — measured, SQLAlchemy refuses outright with
    `InvalidRequestError: Can't invoke Python callable get() inside of lambda expression
    argument`, and tells you to hoist it. So this test is not currently the thing standing
    between us and that bug; the library is.

    It is kept anyway, and named for the risk rather than the error, because it is cheap and
    it covers the case the library's guard does not: a bound value that is correct on first
    execution and stale on the second. The first call populates the statement cache, so if
    that ever regressed the second account would read the first's rows — and the test would
    fail rather than the suite quietly passing.
    """
    Session = sessionmaker(bind=_pg_engine, future=True)
    for account, slug in ((account_a, "cache-a"), (account_b, "cache-b")):
        with account_context(account):
            with Session() as s:
                _property(s, slug)
                s.commit()

    try:
        seen = []
        # Same statement, twice, different accounts. Order matters: the first call is what
        # would poison the cache.
        for account in (account_a, account_b, account_a):
            with account_context(account):
                with Session() as s:
                    seen.append({p.account_id for p in s.query(Property).all()})

        assert seen[0] == {account_a}, f"first account saw {seen[0]}"
        assert seen[1] == {account_b}, (
            f"second account saw {seen[1]} — expected only {account_b}. A cached criteria "
            "lambda is serving the first account's predicate."
        )
        assert seen[2] == {account_a}, f"back to the first account, saw {seen[2]}"
    finally:
        with _pg_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM properties WHERE slug IN ('cache-a','cache-b')")
            )


def test_global_tables_are_queryable_without_an_account(_pg_engine):
    """GLOBAL tables must not require a tenant — sign-in depends on it.

    `users` and `sessions` are GLOBAL precisely because authentication reads them **before**
    any account context exists (D3), and `waitlist` belongs to the standalone landing app,
    whose sessions are the same `Session` class this listener is bound to.

    §4.4's snippet demands an account for *every* ORM statement, which makes sign-in
    impossible and broke all 10 SPEC-001 landing tests when implemented literally. The filter
    now checks `state.all_mappers` and only requires a tenant when a `TenantOwned` entity is
    actually involved.
    """
    from mihomes.models.user import User

    Session = sessionmaker(bind=_pg_engine, future=True)
    with Session() as s:
        # No account context at all — must not raise.
        assert s.query(User).all() == []


def test_join_to_a_tenant_table_is_still_filtered(_pg_engine, account_a, account_b):
    """The other side of that relaxation: a global table joined to a tenant table is scoped.

    Otherwise "query starts from a global entity" would be a bypass — reach any tenant row by
    joining to it from `users`.
    """
    Session = sessionmaker(bind=_pg_engine, future=True)
    for account, slug in ((account_a, "join-a"), (account_b, "join-b")):
        with account_context(account):
            with Session() as s:
                _property(s, slug)
                s.commit()
    try:
        with account_context(account_a):
            with Session() as s:
                rows = s.query(Property).join(
                    Task, Task.property_id == Property.id, isouter=True
                ).all()
                assert {p.account_id for p in rows} <= {account_a}
    finally:
        with _pg_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM properties WHERE slug IN ('join-a','join-b')")
            )


def test_fails_closed_without_context(_pg_engine):
    """No account bound → `LookupError`, not an unscoped query.

    Asserted on a real query rather than on the ContextVar directly: the point is that the
    *listener* refuses, so no caller can reach the database without a tenant.
    """
    Session = sessionmaker(bind=_pg_engine, future=True)
    with Session() as s:
        with pytest.raises(LookupError):
            s.query(Property).all()


# --- A6 / N2: bulk writes are scoped ----------------------------------------------

def test_bulk_ops_scoped(_pg_engine, account_a, account_b):
    """N2 — the case a naive `is_select` guard misses, and the worst one to miss.

    `query(...).delete()` and `.update()` are ORM-enabled bulk statements. If the filter only
    covered SELECT, account A could delete or rewrite **every tenant's rows** in one
    statement. A read leak exposes data; this destroys it.
    """
    Session = sessionmaker(bind=_pg_engine, future=True)
    for account, slug in ((account_a, "bulk-a"), (account_b, "bulk-b")):
        with account_context(account):
            with Session() as s:
                p = _property(s, slug)
                s.add(Task(title=f"task-{slug}", slug=f"task-{slug}", property_id=p.id))
                s.commit()

    try:
        # Account A issues an unqualified bulk UPDATE over Task.
        with account_context(account_a):
            with Session() as s:
                touched = s.query(Task).update(
                    {Task.title: "rewritten"}, synchronize_session=False
                )
                s.commit()
        assert touched == 1, (
            f"bulk update touched {touched} rows from one account — expected 1. The filter "
            "is not covering ORM UPDATE (N2)."
        )

        # B's row must be untouched, checked without the filter in the way.
        with _pg_engine.connect() as conn:
            titles = dict(
                conn.execute(text("SELECT title, account_id::text FROM tasks")).fetchall()
            )
        assert "task-bulk-b" in titles, "account B's task was rewritten by account A"

        # Same again for DELETE.
        with account_context(account_a):
            with Session() as s:
                deleted = s.query(Task).delete(synchronize_session=False)
                s.commit()
        assert deleted == 1, (
            f"bulk delete removed {deleted} rows from one account — expected 1"
        )
        with _pg_engine.connect() as conn:
            remaining = [
                r[0] for r in conn.execute(text("SELECT title FROM tasks")).fetchall()
            ]
        assert "task-bulk-b" in remaining, "account B's task was deleted by account A"
    finally:
        with _pg_engine.begin() as conn:
            conn.execute(text("DELETE FROM tasks"))
            conn.execute(
                text("DELETE FROM properties WHERE slug IN ('bulk-a','bulk-b')")
            )


# --- the escape hatch, and its boundaries -----------------------------------------

def test_skip_tenant_option_bypasses_the_filter(_pg_engine, account_a, account_b):
    """The documented way out, for migrations, the importer, and admin tooling.

    Tested so the bypass is a known quantity rather than folklore — and so that if it ever
    stops working, the paths that depend on it fail here rather than in production.
    """
    Session = sessionmaker(bind=_pg_engine, future=True)
    for account, slug in ((account_a, "skip-a"), (account_b, "skip-b")):
        with account_context(account):
            with Session() as s:
                _property(s, slug)
                s.commit()
    try:
        with account_context(account_a):
            with Session() as s:
                scoped = s.query(Property).all()
                unscoped = (
                    s.query(Property)
                    .execution_options(**{SKIP_TENANT: True})
                    .all()
                )
        assert {p.account_id for p in scoped} == {account_a}
        assert {account_a, account_b} <= {p.account_id for p in unscoped}, (
            "skip_tenant did not bypass the filter"
        )
    finally:
        with _pg_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM properties WHERE slug IN ('skip-a','skip-b')")
            )


def test_association_tables_are_not_covered_by_the_orm_filter():
    """Documents a real gap rather than implying coverage.

    `with_loader_criteria` takes a mapped class; `staff_properties` and `vendor_properties`
    are Core `Table` objects with no class, so the ORM filter cannot reach them at all. Their
    only protection is RLS — which means it is only real on a non-superuser connection.

    If either ever gains a declarative class, this test fails and the docstrings in
    `tenancy/session.py` and `tenancy/rls.py` need updating rather than quietly becoming wrong.
    """
    from mihomes.models import Base, TenantOwned
    from mihomes.tenancy.registry import ASSOCIATION_TABLES

    mapped_tables = {
        m.class_.__tablename__
        for m in Base.registry.mappers
        if issubclass(m.class_, TenantOwned)
    }
    for assoc in ASSOCIATION_TABLES:
        assert assoc not in mapped_tables, (
            f"{assoc} now has a mapped class, so the ORM filter DOES reach it — update the "
            "gap notes in tenancy/session.py and tenancy/rls.py"
        )


def test_context_accessor_never_returns_none(account_a):
    """G8.4 — `require_account()` raises rather than returning None.

    A nullable accessor invites `if account:` at call sites, and every one of those is a
    branch that silently skips scoping.
    """
    from mihomes.tenancy import require_account

    with pytest.raises(LookupError):
        require_account()
    with account_context(account_a):
        assert require_account() == account_a
