"""G7 · §6 Step 7 — row-level security (A8, A9, A10).

**Every test here connects as `app_engine`, not `_pg_engine`, and that is the whole point.**
`_pg_engine` is `postgres`, a superuser, and superusers bypass RLS unconditionally — even
with `FORCE ROW LEVEL SECURITY`. Measured: as `postgres` with the GUC unset, a
FORCE-protected table returned every row. An RLS suite run on that connection passes no
matter what the policies say.

`test_app_role_is_not_a_superuser` is therefore the load-bearing test in this file. Without
it, a later conftest change that repointed `app_engine` at `postgres` would turn everything
below green and meaningless, which is the same shape as the `src/web/` grep blindness and
G6.1's one-ended type gate.
"""

import uuid

import pytest
from sqlalchemy import text

from mihomes.tenancy.registry import TENANT_TABLES
from mihomes.tenancy.rls import MEMBERSHIP_SELF_POLICY, policy_name


@pytest.fixture
def committed(_pg_engine):
    """Create rows that are **committed**, and remove them afterwards.

    RLS tests cannot use the usual roll-back-at-teardown pattern: the whole point is that a
    *second* connection (`app_engine`, as the non-superuser role) reads what the first wrote,
    and an uncommitted row is invisible across connections. So these rows must be committed —
    and therefore must be cleaned up explicitly, because the schema is session-scoped.

    Skipping that cleanup is not hypothetical. The first version of this file leaked committed
    `properties` rows into the shared database, and because G8.1's read filter is still open,
    `list_properties()` is unscoped — so five unrelated tests in `test_web_smoke` and
    `test_form_validation` started failing in the full suite while passing in isolation.
    """
    created: list[tuple[str, uuid.UUID]] = []

    def make(table: str, row_id: uuid.UUID) -> uuid.UUID:
        created.append((table, row_id))
        return row_id

    yield make

    # Reverse order so children go before parents.
    with _pg_engine.begin() as conn:
        for table, row_id in reversed(created):
            conn.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id})


def _make_account(conn, slug: str) -> uuid.UUID:
    account_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO accounts (id, slug, name, type, plan, created_at, updated_at) "
            "VALUES (:id, :slug, :slug, 'household', 'free', now(), now())"
        ),
        {"id": account_id, "slug": slug},
    )
    return account_id


def _make_property(conn, account_id: uuid.UUID, name: str) -> uuid.UUID:
    property_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO properties "
            "(id, account_id, name, slug, property_type, status, currency, occupied) "
            "VALUES (:id, :account_id, :name, :slug, 'PRIMARY', 'OPEN', 'USD', false)"
        ),
        {"id": property_id, "account_id": account_id, "name": name, "slug": name},
    )
    return property_id


# --- the guard on every other test in this file ------------------------------------

def test_app_role_is_not_a_superuser(app_engine):
    """If this fails, every other RLS assertion here is worthless.

    A superuser bypasses RLS with no error and no signal — the rows simply appear. So the
    role has to be asserted, not assumed.
    """
    with app_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT current_user, "
                "(SELECT usesuper FROM pg_user WHERE usename = current_user) AS is_super"
            )
        ).one()
    assert row.is_super is False, (
        f"app_engine connected as {row.current_user!r}, which is a SUPERUSER — RLS is "
        "inert on this connection and every test in this file would pass vacuously"
    )


def test_superuser_really_does_bypass_rls(_pg_engine, app_engine, account_a, committed):
    """Pins the asymmetry the fixture exists for, so it cannot be forgotten.

    Not a test of our code — a test of the assumption the fixture rests on. If a future
    Postgres made FORCE apply to superusers too, this fails and `app_engine`'s docstring
    becomes wrong rather than silently over-cautious.
    """
    with _pg_engine.begin() as conn:
        committed("properties", _make_property(conn, account_a, f"su-{uuid.uuid4().hex[:8]}"))

    with _pg_engine.connect() as conn:
        as_super = conn.execute(text("SELECT count(*) FROM properties")).scalar()
    with app_engine.connect() as conn:
        as_app = conn.execute(text("SELECT count(*) FROM properties")).scalar()

    assert as_super > 0, "superuser should see rows with no GUC set (RLS bypassed)"
    assert as_app == 0, "app role should see nothing with no GUC set"


# --- A8: policies exist, and an unset GUC yields zero rows -------------------------

def test_unset_guc_returns_empty(app_engine, account_a):
    """Step 7's verify clause: **zero rows, not an error, not all rows.**

    `current_setting('app.current_account', true)` — the `true` is `missing_ok`. Without it
    Postgres raises `unrecognized configuration parameter`, and a route that forgot to set
    its context would 500 instead of returning nothing.
    """
    with app_engine.connect() as conn:
        # No error, and no rows.
        assert conn.execute(text("SELECT count(*) FROM properties")).scalar() == 0
        assert conn.execute(
            text("SELECT current_setting('app.current_account', true) IS NULL")
        ).scalar() is True


def test_empty_string_guc_is_treated_as_unset(app_engine, account_a):
    """A GUC set to `''` must behave as "no account", not raise.

    `missing_ok` covers an *absent* setting; it does nothing for one explicitly set to the
    empty string, and `''::uuid` raises `invalid input syntax for type uuid: ""` — an error,
    which is the one outcome Step 7 rules out. `NULLIF` in the policy is what makes the
    predicate total over both spellings of "no account".

    **This is a constraint on G9, which is why it is pinned here.** G9 owns the pool-checkin
    RESET and the `after_begin` GUC, and clearing tenant context by assigning `''` is an
    entirely natural way to write a reset. If it does, this test is what stops that turning
    every query into a 500 instead of an empty result.
    """
    with app_engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_account', '', true)"))
        assert conn.execute(
            text("SELECT current_setting('app.current_account', true) = ''")
        ).scalar() is True

        # Must not raise, and must return nothing.
        assert conn.execute(text("SELECT count(*) FROM properties")).scalar() == 0
        conn.rollback()


def test_every_registry_table_has_a_policy(_pg_engine):
    """A8 · G7.2 — all 40, association tables included.

    Enumerated from `TENANT_TABLES` rather than from `TenantOwned.__subclasses__()`: the two
    Core `Table` association tables are not subclasses, so a derived list would leave
    `staff_properties` and `vendor_properties` with no policy while this test passed.
    """
    with _pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public'")
        ).fetchall()
    by_table = {}
    for table, policy in rows:
        by_table.setdefault(table, set()).add(policy)

    missing = sorted(
        t for t in TENANT_TABLES if policy_name(t) not in by_table.get(t, set())
    )
    assert not missing, f"tenant tables with no RLS policy: {missing}"

    for assoc in ("staff_properties", "vendor_properties"):
        assert assoc in by_table, (
            f"{assoc} has no policy — it is a Core Table, so a __subclasses__()-derived "
            "registry would have skipped it silently"
        )


def test_force_row_level_security_is_set(_pg_engine):
    """`ENABLE` alone does not bind the table owner; `FORCE` does.

    `relforcerowsecurity`, not just `relrowsecurity` — the distinction is the whole reason
    the spec calls for FORCE, and only the second column proves it.
    """
    with _pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = ANY(:names)"
            ),
            {"names": sorted(TENANT_TABLES)},
        ).fetchall()
    not_enabled = sorted(r.relname for r in rows if not r.relrowsecurity)
    not_forced = sorted(r.relname for r in rows if not r.relforcerowsecurity)
    assert not not_enabled, f"RLS not enabled on: {not_enabled}"
    assert not not_forced, f"RLS enabled but not FORCEd on: {not_forced}"


def test_guc_scopes_reads_to_one_account(_pg_engine, app_engine, account_a, account_b, committed):
    """The positive case: with the GUC set, exactly that account's rows are visible."""
    suffix = uuid.uuid4().hex[:8]
    with _pg_engine.begin() as conn:
        committed("properties", _make_property(conn, account_a, f"a-{suffix}"))
        committed("properties", _make_property(conn, account_b, f"b-{suffix}"))

    for account, other in ((account_a, account_b), (account_b, account_a)):
        with app_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_account', :a, true)"),
                {"a": str(account)},
            )
            rows = conn.execute(
                text("SELECT account_id FROM properties")
            ).fetchall()
            seen = {r[0] for r in rows}
            assert seen <= {account}, f"account {account} saw foreign rows: {seen}"
            assert other not in seen


# --- A9: WITH CHECK constrains writes ---------------------------------------------

def test_with_check_rejects_foreign_account(_pg_engine, app_engine, account_a, account_b):
    """A9 — a policy with only USING would let a tenant *write* another's account_id.

    Asserted on the message rather than the exception class: Postgres raises
    `InsufficientPrivilege` for an RLS write violation, but also for an ordinary missing
    grant, so class-only matching would pass if the insert failed for a completely
    different reason.
    """
    with app_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('app.current_account', :a, true)"),
            {"a": str(account_a)},
        )
        with pytest.raises(Exception) as exc:
            conn.execute(
                text(
                    "INSERT INTO properties "
                    "(id, account_id, name, slug, property_type, status, currency, occupied) "
                    "VALUES (:id, :acct, 'Foreign', :slug, 'PRIMARY', 'OPEN', 'USD', false)"
                ),
                # account A's context, account B's account_id.
                {"id": uuid.uuid4(), "acct": account_b, "slug": f"foreign-{uuid.uuid4().hex[:8]}"},
            )
        assert "row-level security" in str(exc.value).lower(), (
            f"insert was rejected, but not by RLS: {exc.value}"
        )
        conn.rollback()


def test_with_check_allows_own_account(_pg_engine, app_engine, account_a):
    """The mirror. Without it, a WITH CHECK that rejected *everything* would look correct."""
    with app_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('app.current_account', :a, true)"),
            {"a": str(account_a)},
        )
        conn.execute(
            text(
                "INSERT INTO properties "
                "(id, account_id, name, slug, property_type, status, currency, occupied) "
                "VALUES (:id, :acct, 'Own', :slug, 'PRIMARY', 'OPEN', 'USD', false)"
            ),
            {"id": uuid.uuid4(), "acct": account_a, "slug": f"own-{uuid.uuid4().hex[:8]}"},
        )
        conn.rollback()


# --- A10: the one bootstrap exception ---------------------------------------------

def test_membership_self_policy(_pg_engine, app_engine, committed):
    """A10 — the account picker must work *before* any account context exists.

    `membership_self` is keyed on a second GUC, `app.current_user`, and is the only
    user-keyed policy in the schema (§4.2). It is SELECT-only: it widens what a user can
    see, never what they can write.

    **The app-side GUC plumbing is G9's, not G7's.** `current_user` exists as a ContextVar
    but nothing sets `app.current_user` on the connection yet — that is G9's `after_begin`
    hook. So this test sets the GUC directly: it verifies the *policy*, and A10 is not truly
    satisfied until G9 wires the GUC. Recorded in the harness as a declared dependency
    rather than left to be discovered when the account picker returns an empty list.
    """
    suffix = uuid.uuid4().hex[:8]
    user_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    with _pg_engine.begin() as conn:
        account_id = _make_account(conn, f"ms-{suffix}")
        # `users` and `memberships` carry no `updated_at` (created_at defaults to now()).
        conn.execute(
            text(
                "INSERT INTO users (id, google_sub, email) VALUES (:id, :sub, :email)"
            ),
            {"id": user_id, "sub": f"sub-{suffix}", "email": f"{suffix}@example.com"},
        )
        conn.execute(
            text(
                "INSERT INTO memberships (id, account_id, user_id, role, status) "
                "VALUES (:id, :acct, :user, 'OWNER', 'ACTIVE')"
            ),
            {"id": membership_id, "acct": account_id, "user": user_id},
        )
    # Registered in dependency order; the fixture deletes in reverse.
    committed("accounts", account_id)
    committed("users", user_id)
    committed("memberships", membership_id)

    with app_engine.connect() as conn:
        # No account context at all — this is the pre-picker state.
        conn.execute(
            text("SELECT set_config('app.current_user', :u, true)"), {"u": str(user_id)}
        )
        rows = conn.execute(
            text("SELECT account_id FROM memberships")
        ).fetchall()
        assert account_id in {r[0] for r in rows}, (
            "membership_self did not expose the user's own membership without an account "
            "context — the account picker cannot work"
        )


def test_membership_self_is_the_only_user_keyed_policy(_pg_engine):
    """§4.2 says keep it to one table, and permissive policies OR together — so a
    user-keyed policy on any other table would punch a hole through that table's account
    scoping for every row that user can reach."""
    with _pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename, policyname, qual FROM pg_policies "
                "WHERE schemaname = 'public'"
            )
        ).fetchall()
    user_keyed = [
        (r.tablename, r.policyname)
        for r in rows
        if r.qual and "app.current_user" in r.qual
    ]
    assert user_keyed == [("memberships", MEMBERSHIP_SELF_POLICY)], (
        f"expected exactly one user-keyed policy, found: {user_keyed}"
    )
