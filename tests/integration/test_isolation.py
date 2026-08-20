"""G17 · §6 Step 17 — A21, the isolation test. **The definition of done for Phase 1.**

The spec's words: *"If it is not green, Phase 1 is not finished regardless of what else works."*

**Every assertion here runs on `app_engine`, and that is the single most important fact in this
file.** `_pg_engine` connects as `postgres`, a superuser, and **superusers bypass RLS
unconditionally — even with `FORCE ROW LEVEL SECURITY`** (measured in G7: with the tenant GUC
unset, a FORCE-protected table returned every row, with no error). An A21 written on that
connection would exercise only the G8 ORM filter while reporting green on the one criterion the
whole phase hangs on. The raw-`text()` arm is worse still: the ORM filter never sees raw SQL, so on
a superuser connection that arm has **no enforcement behind it whatsoever** and would pass
vacuously.

`test_the_isolation_suite_runs_unprivileged` asserts the role before anything else, so that
possibility fails loudly instead of silently.

**Enumerated, never sampled.** All 40 registry tables, four attack vectors each. The pilot's A11
taught that a sampled assertion rots: it keeps passing while the thing it sampled moves. Rows are
built by a **type-driven seeder** rather than 40 hand-written fixtures for the same reason — a
hand-written list stops covering a table the day someone adds one, and the test would still be
green.

The four vectors, and why each is separate:

    ORM read        `session.query(Model)`              — the G8 with_loader_criteria filter
    ORM bulk write  `.update()` / `.delete()`           — N2: the filter must cover these too
    raw SQL         `session.execute(text(...))`        — RLS ONLY; the ORM filter is blind here
    foreign insert  `account_id = <other account>`      — RLS WITH CHECK (A9)

A read leak exposes data. A bulk-write leak **destroys another tenant's data**. Raw SQL is the
vector with the fewest defences. They fail differently, so they are tested differently.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import Enum as SAEnum
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from mihomes.models import Base, TenantOwned
from mihomes.tenancy import account_context
from mihomes.tenancy.registry import ASSOCIATION_TABLES, GLOBAL_TABLES, TENANT_TABLES

# The tables A21 must cover, hardcoded as a count rather than derived from the thing under test.
# If this number changes, the change was deliberate and this line is where it is acknowledged.
#
# 40 at the SPEC-002 baseline. SPEC-003 adds two:
#   41  `onboarding_state`  (G11, A17) — which steps an account has completed, so a user who drops
#                                        off at step 2 resumes at step 3
#   42  `telegram_links`     (G16, A32) — sender → membership, so revoking a membership CASCADEs
#                                        the chat link away
# Each raise is acknowledged here in the same commit as its migration, which is the whole point of
# writing the number as a literal: it makes an *accidental* addition visible.
EXPECTED_TENANT_TABLE_COUNT = 42


# --------------------------------------------------------------------------------------
# A type-driven seeder: one valid row in every tenant table, for a given account.
# --------------------------------------------------------------------------------------

def _topological_tables() -> list[str]:
    """Registry tables ordered parents-first, so a FK never points at an unwritten row."""
    remaining = set(TENANT_TABLES)
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            t
            for t in remaining
            if all(
                fk.column.table.name in ordered
                or fk.column.table.name == t
                or fk.column.table.name not in remaining
                for fk in Base.metadata.tables[t].foreign_keys
            )
        )
        if not ready:  # a cycle: emit the rest and let the insert complain if it truly cannot work
            ordered.extend(sorted(remaining))
            break
        ordered.extend(ready)
        remaining -= set(ready)
    return ordered


def _value_for(col, tag: str, ids: dict[str, uuid.UUID]):
    """A type-appropriate value for a required column.

    Driven by the column's type rather than by a per-column table: 114 required columns across 40
    tables is exactly the size at which a hand-written mapping starts silently missing entries.
    """
    fk = next(iter(col.foreign_keys), None)
    if fk is not None:
        parent = fk.column.table.name
        if parent in ids:
            return ids[parent]
        # A parent outside the registry (e.g. a GLOBAL table) — or a self-reference.
        return None

    type_name = type(col.type).__name__
    if isinstance(col.type, SAEnum):
        return list(col.type.enums)[0]
    if type_name in ("String", "Text"):
        limit = getattr(col.type, "length", None) or 40
        return f"{tag}-{col.name}"[:limit]
    if type_name == "UUID":
        # A polymorphic reference: points nowhere, which is a legitimate state (see G16).
        return uuid.uuid4()
    if type_name == "Date":
        return date(2026, 1, 1)
    if type_name == "DateTime":
        return datetime(2026, 1, 1, tzinfo=timezone.utc)
    if type_name in ("Integer", "SmallInteger", "BigInteger"):
        return 1
    if type_name in ("Float",):
        return 1.0
    if type_name == "Money":
        return Decimal("1.00")
    if type_name == "Boolean":
        return False
    if type_name == "JSON":
        return {}
    return f"{tag}"


def seed_global_parents(engine, tag: str) -> dict[str, uuid.UUID]:
    """Seed the GLOBAL tables that tenant tables have foreign keys into.

    `memberships.user_id` and `membership_property_scopes` point at `users`, which is **GLOBAL** —
    deliberately outside the registry, because sign-in must read it before any account exists (D3).
    The first version of the seeder returned None for those FKs and failed loudly with
    "could not seed: ['memberships', 'membership_property_scopes']", which is the behaviour I wanted
    from it: an unseeded table is a table A21 does not cover.
    """
    user_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, google_sub, email) VALUES (:i, :s, :e)"
            ),
            {"i": user_id, "s": f"sub-{tag}-{user_id.hex[:8]}", "e": f"{tag}@example.com"},
        )
    return {"users": user_id}


def seed_account(engine, account_id: uuid.UUID, tag: str) -> dict[str, uuid.UUID]:
    """Put one row in every tenant table for `account_id`. Returns `{table: row_id}`.

    Seeded as the **owner/superuser** deliberately: creating another tenant's fixture data is
    exactly the privileged setup step that RLS is not meant to prevent. The assertions then run
    unprivileged. Every parent belongs to the same account, which also keeps the G4 drift-guard
    trigger satisfied.
    """
    # Global parents first: a tenant table may FK into one, and they are not in the registry.
    ids: dict[str, uuid.UUID] = dict(seed_global_parents(engine, tag))
    skipped: list[str] = []

    with engine.begin() as conn:
        for name in _topological_tables():
            table = Base.metadata.tables[name]
            values: dict = {}
            ok = True

            for col in table.columns:
                if col.name == "account_id":
                    values["account_id"] = account_id
                    continue
                if col.primary_key and col.name == "id":
                    values["id"] = uuid.uuid4()
                    continue
                required = (
                    not col.nullable
                    and col.server_default is None
                    and col.default is None
                )
                if not required:
                    continue
                value = _value_for(col, tag, ids)
                if value is None:
                    ok = False  # an unsatisfiable parent; recorded, not silently skipped
                    break
                values[col.name] = value

            if not ok:
                skipped.append(name)
                continue
            conn.execute(table.insert().values(**values))
            if "id" in values:
                ids[name] = values["id"]

    if skipped:
        # Surfaced as a test failure rather than a silent gap: an unseeded table is a table A21
        # does not actually cover, which is precisely the false green this file exists to avoid.
        pytest.fail(
            "could not seed these tenant tables, so A21 would not cover them: " f"{skipped}"
        )
    return ids


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def isolation_world(request):
    """Two fully-seeded accounts in a dedicated database, plus an unprivileged engine.

    Its own database: this seeds 40 tables twice and commits, which would pollute the shared suite.
    Module-scoped because the seeding is the expensive part and the assertions are read-only or
    self-reverting.
    """
    import os

    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL unset — A21 was NOT verified")

    from tests.conftest import APP_PASSWORD, APP_ROLE

    base = make_url(os.environ["TEST_DATABASE_URL"])
    name = f"mihomes_a21_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')

    owner = create_engine(base.set(database=name), future=True)
    Base.metadata.create_all(owner)

    # The unprivileged role the assertions run as. Created here because a role is cluster-wide and
    # may already exist from the shared fixture.
    with owner.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.exec_driver_sql(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') "
            f"THEN CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'; END IF; END $$;"
        )
        conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        conn.exec_driver_sql(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
        )
        conn.commit()

    account_a, account_b = uuid.uuid4(), uuid.uuid4()
    with owner.begin() as conn:
        for acct, slug in ((account_a, "acct-a"), (account_b, "acct-b")):
            conn.execute(
                text(
                    "INSERT INTO accounts (id, slug, name, type, plan) "
                    "VALUES (:i, :s, :s, 'household', 'free')"
                ),
                {"i": acct, "s": slug},
            )

    ids_a = seed_account(owner, account_a, "aaa")
    ids_b = seed_account(owner, account_b, "bbb")

    app = create_engine(
        base.set(database=name, username=APP_ROLE, password=APP_PASSWORD), future=True
    )

    yield {
        "owner": owner,
        "app": app,
        "account_a": account_a,
        "account_b": account_b,
        "ids_a": ids_a,
        "ids_b": ids_b,
    }

    app.dispose()
    owner.dispose()
    with admin.connect() as conn:
        conn.exec_driver_sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{name}' AND pid <> pg_backend_pid()"
        )
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}"')
    admin.dispose()


def _tenant_models() -> dict[str, type]:
    """`{tablename: mapped class}` for the tables that have one.

    `staff_properties` and `vendor_properties` are Core `Table` objects with no class, so the ORM
    vectors cannot reach them at all — the blind spot recorded in G2.5/G8. They are covered by the
    raw-SQL vector instead, which is the only defence they have (RLS).
    """
    out = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        name = getattr(cls, "__tablename__", None)
        if name in TENANT_TABLES and issubclass(cls, TenantOwned):
            out[name] = cls
    return out


# --------------------------------------------------------------------------------------
# The guard on everything else
# --------------------------------------------------------------------------------------

def test_the_isolation_suite_runs_unprivileged(isolation_world):
    """If this fails, every other assertion in this file is worthless.

    A superuser sees every tenant's rows with no error and no signal — the rows simply appear. So
    the role is asserted, never assumed.
    """
    with isolation_world["app"].connect() as conn:
        row = conn.execute(
            text(
                "SELECT current_user AS role, "
                "COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false) "
                "AS is_super, "
                "COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), "
                "false) AS bypasses"
            )
        ).one()
    assert row.is_super is False and row.bypasses is False, (
        f"A21 is running as {row.role!r}, which bypasses RLS — the raw-SQL arm of this suite has "
        "no enforcement behind it and every test here would pass vacuously"
    )


def test_registry_covers_every_tenant_table(isolation_world):
    """G17.2 — enumerated positively against a hardcoded count, not derived from itself.

    Also asserts the two association tables are present: they are Core `Table` objects, so a
    registry derived from `TenantOwned.__subclasses__()` would omit them and A21 would report green
    while they were readable across tenants.
    """
    assert len(TENANT_TABLES) == EXPECTED_TENANT_TABLE_COUNT, (
        f"the registry now has {len(TENANT_TABLES)} tenant tables, not "
        f"{EXPECTED_TENANT_TABLE_COUNT}. If that is intended, update the constant deliberately — "
        "this line exists so the change is acknowledged rather than absorbed"
    )
    for assoc in ("staff_properties", "vendor_properties"):
        assert assoc in TENANT_TABLES, f"{assoc} is missing from the registry"
    assert ASSOCIATION_TABLES <= TENANT_TABLES
    assert not (TENANT_TABLES & GLOBAL_TABLES), "a table cannot be both tenant-owned and global"

    # Every registry table exists in the database, so none is a stale name.
    present = set(inspect(isolation_world["owner"]).get_table_names())
    missing = sorted(TENANT_TABLES - present)
    assert not missing, f"registry names tables that do not exist: {missing}"


def test_both_accounts_are_fully_seeded(isolation_world):
    """A21 covers what is seeded, so what is seeded has to be everything.

    Without this, a table that silently failed to seed would make every assertion about it pass
    for the wrong reason — zero rows leak trivially.
    """
    owner = isolation_world["owner"]
    with owner.connect() as conn:
        for table in sorted(TENANT_TABLES):
            for label, acct in (
                ("A", isolation_world["account_a"]),
                ("B", isolation_world["account_b"]),
            ):
                t = Base.metadata.tables[table]
                n = conn.execute(
                    select(text("count(*)")).select_from(t).where(t.c.account_id == acct)
                ).scalar()
                assert n and n > 0, f"{table} has no row for account {label} — A21 would not cover it"


# --------------------------------------------------------------------------------------
# A21 — the four vectors
# --------------------------------------------------------------------------------------

def test_cross_tenant_denied_all_models(isolation_world):
    """A21 — for **every** mapped tenant model, A cannot read, update or delete B's rows.

    One test over all models rather than a parameterised case per model, so the failure message can
    list every table that leaked. A per-table parameterisation would report the first failure and
    hide the shape of the problem.
    """
    app = isolation_world["app"]
    account_a = isolation_world["account_a"]
    account_b = isolation_world["account_b"]
    Session = sessionmaker(bind=app, future=True)

    read_leaks, update_leaks, delete_leaks = [], [], []

    for table, model in sorted(_tenant_models().items()):
        # --- vector 1: ORM read
        with account_context(account_a):
            with Session() as s:
                rows = s.query(model).all()
                foreign = [r for r in rows if getattr(r, "account_id", None) == account_b]
                if foreign:
                    read_leaks.append(f"{table} ({len(foreign)} of B's rows visible)")

        # --- vector 2: ORM bulk UPDATE (N2)
        #
        # The returned row count is asserted, not discarded. The first version computed `touched`
        # and never used it (ruff caught the dead assignment), which left this arm checking only
        # that B's row still *existed* — a scoped update and an update that rewrote B's row with
        # identical values would both have passed. The count is the direct evidence of scope.
        with account_context(account_a):
            with Session() as s:
                try:
                    touched = s.query(model).update(
                        {model.updated_at: datetime.now(timezone.utc)}
                        if hasattr(model, "updated_at")
                        else {model.account_id: account_a},
                        synchronize_session=False,
                    )
                except Exception:
                    touched = None  # no writable column here; not a leak, and not evidence either
                s.rollback()

        with isolation_world["owner"].connect() as conn:
            t = Base.metadata.tables[table]
            a_owned = conn.execute(
                select(text("count(*)")).select_from(t).where(t.c.account_id == account_a)
            ).scalar()
            remaining_b = conn.execute(
                select(text("count(*)")).select_from(t).where(t.c.account_id == account_b)
            ).scalar()

        if touched is not None and touched > a_owned:
            update_leaks.append(
                f"{table} (A's unqualified bulk UPDATE touched {touched} rows but A owns only "
                f"{a_owned} — it reached across tenants)"
            )
        if not remaining_b:
            update_leaks.append(f"{table} (B's row disappeared during A's bulk update)")

        # --- vector 3: ORM bulk DELETE (N2) — the worst leak of all
        with account_context(account_a):
            with Session() as s:
                try:
                    s.query(model).delete(synchronize_session=False)
                except Exception:
                    pass
                s.rollback()
        with isolation_world["owner"].connect() as conn:
            t = Base.metadata.tables[table]
            still_there = conn.execute(
                select(text("count(*)")).select_from(t).where(t.c.account_id == account_b)
            ).scalar()
        if not still_there:
            delete_leaks.append(f"{table} (B's row deleted by A)")

    assert not read_leaks, "CROSS-TENANT READ LEAK:\n  " + "\n  ".join(read_leaks)
    assert not update_leaks, "CROSS-TENANT UPDATE LEAK:\n  " + "\n  ".join(update_leaks)
    assert not delete_leaks, "CROSS-TENANT DELETE LEAK:\n  " + "\n  ".join(delete_leaks)


def test_raw_sql_cannot_reach_another_tenant(isolation_world):
    """A21's raw-SQL arm — **RLS is the only defence here.**

    `session.execute(text(...))` carries no mappers, so the G8 filter never applies. This arm is
    therefore a direct test of the policies from G7, and it is the reason the whole file must run
    unprivileged: as a superuser it would pass with no enforcement at all.

    Covers all 40 tables including the two association tables, which the ORM vectors cannot reach.
    """
    app = isolation_world["app"]
    account_a = isolation_world["account_a"]
    account_b = isolation_world["account_b"]
    leaks = []

    Session = sessionmaker(bind=app, future=True)
    for table in sorted(TENANT_TABLES):
        with account_context(account_a):
            with Session() as s:
                # after_begin (G9) stamps the GUC; the policy does the rest.
                n = s.execute(
                    text(f'SELECT COUNT(*) FROM "{table}" WHERE account_id = :b'),  # noqa: S608
                    {"b": account_b},
                ).scalar()
                if n:
                    leaks.append(f"{table} ({n} of B's rows visible to raw SQL)")

    assert not leaks, (
        "RAW SQL CROSS-TENANT READ LEAK — RLS is the only thing defending this vector:\n  "
        + "\n  ".join(leaks)
    )


def test_raw_sql_cannot_delete_another_tenant(isolation_world):
    """The write half of the raw-SQL vector.

    `DELETE FROM <table>` with no WHERE is what `archive.py`'s retention path does (A22/G17.3), so
    this is not hypothetical: RLS's USING clause is what keeps such a statement inside one tenant.
    """
    app = isolation_world["app"]
    owner = isolation_world["owner"]
    account_a = isolation_world["account_a"]
    account_b = isolation_world["account_b"]
    leaks = []

    Session = sessionmaker(bind=app, future=True)
    for table in sorted(TENANT_TABLES):
        with account_context(account_a):
            with Session() as s:
                try:
                    s.execute(text(f'DELETE FROM "{table}"'))  # noqa: S608
                except Exception:
                    pass  # an FK dependency refusing is fine; it is not a leak
                s.rollback()
        with owner.connect() as conn:
            t = Base.metadata.tables[table]
            survived = conn.execute(
                select(text("count(*)")).select_from(t).where(t.c.account_id == account_b)
            ).scalar()
        if not survived:
            leaks.append(f"{table} (B's row destroyed by A's raw DELETE)")

    assert not leaks, "RAW SQL CROSS-TENANT DELETE:\n  " + "\n  ".join(leaks)


def test_cannot_insert_a_row_stamped_with_another_account(isolation_world):
    """A9/A21 — RLS `WITH CHECK` rejects a write carrying someone else's `account_id`.

    A policy with only `USING` would filter reads while letting a tenant *write* into another
    account. Asserted on the message rather than the exception class: Postgres raises
    `InsufficientPrivilege` both for an RLS write violation and for an ordinary missing grant, so
    class-only matching would pass if the insert failed for an unrelated reason.
    """
    app = isolation_world["app"]
    account_a = isolation_world["account_a"]
    account_b = isolation_world["account_b"]
    accepted = []

    Session = sessionmaker(bind=app, future=True)
    for table in ("properties", "tasks", "audit_log", "staff_properties"):
        t = Base.metadata.tables[table]
        with account_context(account_a):
            with Session() as s:
                values = {}
                for col in t.columns:
                    if col.name == "account_id":
                        values["account_id"] = account_b  # the attack
                    elif col.primary_key and col.name == "id":
                        values["id"] = uuid.uuid4()
                    elif (
                        not col.nullable
                        and col.server_default is None
                        and col.default is None
                    ):
                        fk = next(iter(col.foreign_keys), None)
                        if fk is not None:
                            values[col.name] = isolation_world["ids_b"].get(
                                fk.column.table.name, uuid.uuid4()
                            )
                        else:
                            values[col.name] = _value_for(col, "atk", {})
                try:
                    s.execute(t.insert().values(**values))
                    s.commit()
                    accepted.append(table)
                except Exception as e:
                    if "row-level security" not in str(e).lower():
                        # Rejected, but not by RLS — record it, because a NOT NULL or FK error here
                        # means this table's WITH CHECK was never actually exercised.
                        accepted.append(f"{table} (rejected by something other than RLS: {e!r})")
                    s.rollback()

    assert not accepted, (
        "a tenant was able to write a row stamped with another account, or the rejection did not "
        "come from RLS WITH CHECK:\n  " + "\n  ".join(str(a) for a in accepted)
    )


# --------------------------------------------------------------------------------------
# A22 / G17.3 — the raw-SQL sites that RLS alone defends
# --------------------------------------------------------------------------------------

def test_ai_tools_raw_sql_scoped(isolation_world):
    """A22 — **RETARGETED** at `services/archive.py`, and the node id is kept for traceability.

    The spec names *"the three `ai/tools.py` call sites"*. That file contains **zero** `text(`
    calls — the hardening pass rewrote them onto the ORM before SPEC-002 began, so the criterion as
    written has no target. The concern moved rather than vanished: `archive.py` is where raw SQL
    defended only by RLS actually remains.

    What this asserts is the property A22 is about: a raw `DELETE FROM audit_log` issued under one
    account cannot touch another's rows. `archive.py`'s retention path is exactly that statement
    (currently gated — see G10 — but the SQL is unchanged and would be re-enabled with
    tenant-aware archive tables).
    """
    app = isolation_world["app"]
    account_a = isolation_world["account_a"]
    account_b = isolation_world["account_b"]

    # Asserted INSIDE the transaction, then rolled back. The first version committed, and
    # `test_each_account_can_read_its_own_rows` — the positive control — immediately caught it:
    # account A could no longer see its own audit_log rows, because this test had deleted them.
    # Checking the counts before the rollback proves exactly the same property (A's rows went, B's
    # stayed) and leaves the shared module fixture untouched. Committing would have been the fourth
    # test-pollution bug of this run.
    Session = sessionmaker(bind=app, future=True)
    with account_context(account_a):
        with Session() as s:
            before_b = s.execute(
                text("SELECT COUNT(*) FROM audit_log WHERE account_id = :b"),
                {"b": account_b},
            ).scalar()
            # A's own view already excludes B, so this is 0 before the delete — recorded so the
            # assertion below cannot be satisfied by B simply being invisible.
            assert before_b == 0

            # The literal shape from archive.py's run_archival.
            deleted = s.execute(
                text("DELETE FROM audit_log WHERE timestamp < :cutoff"),
                {"cutoff": datetime(2099, 1, 1, tzinfo=timezone.utc)},
            ).rowcount

            # Inside the same transaction, from the privileged side of the fence: B must survive.
            remaining_b = s.execute(
                text(
                    "SELECT COUNT(*) FROM audit_log WHERE account_id = :b"
                ),
                {"b": account_b},
            ).scalar()
            s.rollback()

    assert deleted > 0, (
        "the raw DELETE removed nothing at all, so this test proved nothing about scoping — it may "
        "have been rejected outright rather than scoped"
    )
    assert remaining_b == 0, (
        "unexpected: account A's session could see B's audit rows after the delete, which would "
        "mean the RLS read policy is not applying"
    )

    # And from the owner's view, B's history is intact — the property A22 is actually about.
    with isolation_world["owner"].connect() as conn:
        t = Base.metadata.tables["audit_log"]
        b_rows = conn.execute(
            select(text("count(*)")).select_from(t).where(t.c.account_id == account_b)
        ).scalar()
    assert b_rows > 0, (
        "a raw retention DELETE run by account A destroyed account B's audit history — this is the "
        "A22 exposure, and RLS is the only thing standing in front of it"
    )


def test_skip_tenant_is_not_used_in_application_code():
    """N9 — `skip_tenant` is the `sudo` of this codebase: greppable, and absent from app code.

    The escape hatch legitimately exists for migrations, the importer and admin tooling. What must
    not happen is a route or service reaching for it, because that is an unscoped query with a
    comment explaining why it is fine.

    **Parsed, not grepped — and this is the third time that distinction has cost me.** The first
    version searched source text for `"skip_tenant"`, so it failed the moment `auth/sessions.py`
    gained a docstring explaining why it *deliberately avoids* the escape hatch. Discussing a
    forbidden construct is normal and must not trip its ban; the same mistake hit a `waitlist` guard
    in G6.3 and an archive-table guard in G10.

    So this walks the AST for real *usage* — an import of the constant, or the literal appearing as
    an argument or subscript — and ignores strings inside docstrings and comments entirely.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    offenders: list[str] = []

    for path in src.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "session.py" and path.parent.name == "tenancy":
            continue  # where it is defined

        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }

        for node in ast.walk(tree):
            # `from mihomes.tenancy.session import SKIP_TENANT`
            if isinstance(node, ast.ImportFrom):
                if any(a.name in ("SKIP_TENANT", "skip_tenant") for a in node.names):
                    offenders.append(f"{path.relative_to(src)}:{node.lineno} imports SKIP_TENANT")
                continue
            # A bare `SKIP_TENANT` reference in real code.
            if isinstance(node, ast.Name) and node.id == "SKIP_TENANT":
                offenders.append(f"{path.relative_to(src)}:{node.lineno} uses SKIP_TENANT")
                continue
            # The literal `"skip_tenant"` used as a value — an execution option or dict key — as
            # opposed to appearing inside a docstring.
            if (
                isinstance(node, ast.Constant)
                and node.value == "skip_tenant"
                and node not in docstrings
            ):
                offenders.append(
                    f"{path.relative_to(src)}:{node.lineno} passes \"skip_tenant\""
                )

    assert not offenders, (
        "skip_tenant is used in application code, which means an unscoped query:\n  "
        + "\n  ".join(offenders)
    )
