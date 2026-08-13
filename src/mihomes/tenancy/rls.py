"""G7 · §6 Step 7 — row-level security, generated from the tenancy registry.

Every tenant table gets one policy pair keyed on a transaction-local GUC:

    USING      (account_id = (SELECT current_setting('app.current_account', true)::uuid))
    WITH CHECK (account_id = (SELECT current_setting('app.current_account', true)::uuid))

**Three details in that expression are load-bearing, not style.**

* ``true`` is ``missing_ok``. With it, an unset GUC yields ``NULL``, the predicate is
  ``NULL``, and the query returns **zero rows**. Without it, Postgres raises
  ``unrecognized configuration parameter``. Step 7's verify clause is precisely "zero rows,
  not an error" — fail *closed and quiet* rather than closed and loud, because a route that
  forgot its context should return nothing, not a 500.
* ``NULLIF(..., '')`` covers a case ``missing_ok`` does not: a GUC that is **set to the
  empty string**. ``missing_ok`` only makes an *absent* setting return ``NULL``; an
  explicitly empty one returns ``''``, and ``''::uuid`` raises
  ``invalid input syntax for type uuid: ""`` — an error, which is the one outcome Step 7
  rules out.

  What is measured, precisely, because the obvious theory about this is wrong:
  ``current_setting('app.current_account', true)`` returns ``NULL`` when unset **and still
  NULL after another ``app.*`` GUC has been set** — there is no "known prefix" effect. It
  returns ``''`` only when something explicitly sets it to ``''``. The unguarded cast was
  nonetheless observed failing with exactly that error on
  ``SELECT account_id FROM memberships`` while ``app.current_user`` was set, so some path in
  that flow does supply an empty string; ``NULLIF`` makes the predicate total over both
  spellings of "no account" instead of depending on which one arrives.

  **This is a live constraint on G9, not just defensive coding.** G9 owns the pool-checkin
  ``RESET`` and the ``after_begin`` GUC. Clearing tenant context by assigning ``''`` — an
  entirely natural way to write a reset — produces precisely this state, and without
  ``NULLIF`` it turns every subsequent query into a 500 rather than an empty result. G9
  should clear with ``RESET``/``set_config(..., NULL, ...)`` and there is a test pinning the
  empty-string behaviour either way.
* The ``(SELECT ...)`` wrapper forces an InitPlan, evaluated **once per query** instead of
  once per row. Dropping the parentheses is a silent per-row function call on every scan.
* ``FORCE ROW LEVEL SECURITY`` in addition to ``ENABLE``: plain RLS does not apply to the
  table **owner**, and the app connects as a role that may own its tables in some
  deployments. FORCE closes that.

**What FORCE does NOT close: superusers bypass RLS unconditionally.** Measured on this
cluster — as ``postgres`` with the GUC unset, a FORCE-protected table returned all rows.
That is why RLS cannot be verified from the suite's ordinary ``postgres`` connection, and
why `tests/conftest.py` grows an ``app_session`` fixture on a dedicated non-superuser role.
**Any test that means to prove tenant isolation — A21 above all — must use it.** A21 run as
``postgres`` proves only that the G8 ORM filter works, while reporting that RLS does.

N5 is the same fact from the other side: the runtime role must not be an owner or
``BYPASSRLS`` role, while migrations legitimately run as the owner and bypass policies.

**Role creation is deliberately not here.** A role is cluster-wide, not per-database, so
``CREATE ROLE`` inside a migration would collide the second time the migration ran against
another database in the same cluster. Production provisioning owns it; the test fixture
creates one idempotently. This module only ever emits per-table DDL.
"""

from __future__ import annotations

from sqlalchemy import MetaData, event

# The account predicate, written once. Both USING and WITH CHECK use it: USING filters what
# a query can see, WITH CHECK constrains what it may write, and a policy with only USING
# would let a tenant INSERT rows stamped with someone else's account (A9).
_ACCOUNT_PREDICATE = (
    "account_id = (SELECT NULLIF(current_setting('app.current_account', true), '')::uuid)"
)

# A10 — the single bootstrap exception, and the only user-keyed policy in the schema.
#
# The account picker has to list "which accounts do I belong to?" *before* any account
# context exists, so that one read cannot be account-scoped. It is keyed on a second GUC,
# `app.current_user`, and is SELECT-only: it widens visibility, never write permission.
#
# §4.2 says keep it to this one table. Permissive policies OR together, so adding a
# user-keyed policy to any other table would quietly punch a hole through that table's
# account scoping for every row the user could reach.
MEMBERSHIP_SELF_POLICY = "membership_self"
MEMBERSHIPS_TABLE = "memberships"


def policy_name(table: str) -> str:
    return f"{table}_tenant"


def policy_statements(table: str) -> list[str]:
    """The DDL for one tenant table. Idempotent, so `create_all` can re-run it."""
    name = policy_name(table)
    stmts = [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {name} ON {table}",
        f"CREATE POLICY {name} ON {table} "
        f"USING ({_ACCOUNT_PREDICATE}) WITH CHECK ({_ACCOUNT_PREDICATE})",
    ]
    if table == MEMBERSHIPS_TABLE:
        stmts += [
            f"DROP POLICY IF EXISTS {MEMBERSHIP_SELF_POLICY} ON {table}",
            f"CREATE POLICY {MEMBERSHIP_SELF_POLICY} ON {table} FOR SELECT "
            "USING (user_id = (SELECT NULLIF("
            "current_setting('app.current_user', true), '')::uuid))",
        ]
    return stmts


def rls_statements() -> list[str]:
    """Policy DDL for every table in the registry.

    Generated from `TENANT_TABLES` rather than from `TenantOwned.__subclasses__()`, which is
    the whole reason the registry is explicit: the two Core `Table` association tables are
    not subclasses, so a derived list would leave `staff_properties` and `vendor_properties`
    with **no policy at all** while A8's "every table has a policy" test passed.
    """
    from mihomes.tenancy.registry import TENANT_TABLES

    stmts: list[str] = []
    for table in sorted(TENANT_TABLES):
        stmts.extend(policy_statements(table))
    return stmts


def drop_statements() -> list[str]:
    from mihomes.tenancy.registry import TENANT_TABLES

    stmts: list[str] = []
    for table in sorted(TENANT_TABLES):
        stmts.append(f"DROP POLICY IF EXISTS {policy_name(table)} ON {table}")
        if table == MEMBERSHIPS_TABLE:
            stmts.append(
                f"DROP POLICY IF EXISTS {MEMBERSHIP_SELF_POLICY} ON {table}"
            )
        stmts.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        stmts.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return stmts


def install_rls(metadata: MetaData) -> None:
    """Emit the policies on `create_all`, Postgres only.

    Same reasoning as `install_drift_guard`: the suite builds its schema with `create_all`,
    so policies defined only in `0002_rls` would be absent from every test database and an
    RLS test would pass against a table with no policy on it.
    """

    def _emit(target, connection, **kw):
        if connection.dialect.name != "postgresql":
            return
        for stmt in rls_statements():
            connection.exec_driver_sql(stmt)

    event.listen(metadata, "after_create", _emit)
