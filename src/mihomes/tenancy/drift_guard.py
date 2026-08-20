"""G4 · §6 Step 4 — a child row's ``account_id`` may not diverge from its parent's.

**The guarantee** the spec asks for is *"a test attempts to write a child row whose
``account_id`` differs from its parent's and asserts it is rejected"* — enforcement in the
database, not in the application. Step 4 *suggests* a composite FK
``(account_id, parent_id) -> (account_id, id)`` as the mechanism. That mechanism was built
and measured, and it does not work here:

* Adding a composite FK **alongside** the existing single-column FK gives two FK paths
  between the same pair of tables and SQLAlchemy raises ``AmbiguousForeignKeysError``.
* **Replacing** the single-column FK does configure — but it makes ``account_id`` a write
  target for every relationship into the child, and SQLAlchemy warns that
  ``Transaction.vendor`` and ``Transaction.property`` both copy ``<parent>.account_id``
  into ``transactions.account_id``. Silencing that needs an ``overlaps=`` annotation on
  each of the codebase's **53** relationships, and the underlying write ambiguity is real
  rather than cosmetic.

So the guarantee is delivered by a trigger instead, which the spec explicitly sanctions for
the case where a composite FK is impossible. It costs no ORM churn, applies uniformly to
the two Core ``Table`` association tables that a ``__table_args__`` edit could not reach,
and enforces at the same layer a composite FK would.

**MATCH SIMPLE semantics are reproduced deliberately.** A composite FK with a NULL column
is satisfied trivially, so an optional parent that is NULL must be accepted. The ``IS NULL``
early return below is what preserves that; without it every optional relationship breaks.

**The DDL is attached to ``Base.metadata``, not only to the migration.** The test suite
builds its schema with ``create_all`` (see ``tests/conftest.py``), which does not create
triggers — so a guard defined only in the migration would be absent from every test
database and the drift test would pass against an unguarded schema. G6.2's migration must
import ``DRIFT_GUARD_FUNCTION`` and ``trigger_ddl_statements()`` from here rather than
copy the SQL, or the two will drift.

**Not covered: the four polymorphic tables** (``notes``, ``documents``, ``audit_log``,
``tag_assignments``), which carry ``entity_type`` + ``entity_id`` with no FK. A trigger
would need an ``entity_type`` -> table mapping in SQL, and no authoritative mapping exists
to derive it from — the three tables use three inconsistent vocabularies. Measured:
``notes`` says ``"workorder"`` where ``audit_log`` says ``"work_order"``, ``audit_log``
carries 22 distinct values to ``documents``' 9, and ``documents`` uses ``"ha_entity"`` for
a table that does not exist at all. Inventing a unified mapping would either reject
legitimate rows or silently skip them, which is worse than not claiming the guarantee.

**Residual exposure for those four, stated rather than implied:** their ``account_id`` is
set by the G8.3 ``before_flush`` listener from the session's account, so an ORM insert
lands in the writer's tenant. What is unguarded is (a) a raw-SQL insert, and (b) an ORM
insert whose ``entity_id`` was read from another tenant's row — the child would be stamped
with the *writer's* account while pointing at a foreign parent. **A21 does not cover this**;
it is app-level enforcement only.
"""

from __future__ import annotations

from sqlalchemy import DDL, MetaData, event

# The one function every trigger shares. Parameterised by trigger arguments rather than
# generated per table, so there is a single definition of the comparison.
#
# ERRCODE 23514 (check_violation) makes psycopg raise IntegrityError, so a caller cannot
# tell a drift rejection from any other constraint violation by exception type — which is
# correct: it *is* a constraint violation.
#
# Deliberately contains no ``%`` anywhere. psycopg3 scans statement text for client-side
# placeholders and rejects anything that is not ``%s``/``%b``/``%t`` — a ``format('...%I')``
# body fails with *"only '%s', '%b', '%t' are allowed as placeholders, got '%I'"* before
# Postgres ever sees it. Rather than escape (which then depends on whether the driver was
# handed an empty parameter tuple or None), the SQL avoids ``%`` entirely:
# ``to_jsonb(NEW)`` for the dynamic field read, ``quote_ident`` concatenation for the
# lookup, and ``RAISE ... USING MESSAGE`` instead of a ``%``-formatted message.
DRIFT_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION mihomes_assert_account_matches_parent()
RETURNS trigger AS $mihomes_drift$
DECLARE
    parent_table   text := TG_ARGV[0];
    fk_column      text := TG_ARGV[1];
    fk_value       uuid;
    parent_account uuid;
BEGIN
    -- Dynamic field read without format(): NEW as jsonb, keyed by the column name.
    fk_value := (to_jsonb(NEW) ->> fk_column)::uuid;

    -- MATCH SIMPLE: a NULL optional parent has nothing to diverge from.
    IF fk_value IS NULL THEN
        RETURN NEW;
    END IF;

    EXECUTE 'SELECT account_id FROM ' || quote_ident(parent_table) || ' WHERE id = $1'
        INTO parent_account USING fk_value;

    -- No such parent: that is the FK constraint's job to report, not this trigger's.
    IF parent_account IS NULL THEN
        RETURN NEW;
    END IF;

    IF parent_account <> NEW.account_id THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'tenant drift on ' || TG_TABLE_NAME || '.' || fk_column
                      || ': parent ' || fk_value
                      || ' belongs to account ' || parent_account
                      || ', child claims ' || NEW.account_id;
    END IF;

    RETURN NEW;
END;
$mihomes_drift$ LANGUAGE plpgsql;
"""

DROP_DRIFT_GUARD_FUNCTION = (
    "DROP FUNCTION IF EXISTS mihomes_assert_account_matches_parent() CASCADE"
)


def trigger_name(child: str, fk_column: str) -> str:
    """Postgres caps identifiers at 63 bytes; the longest pair here lands near 50."""
    return f"trg_{child}_{fk_column}_account"[:63]


def parent_links(metadata: MetaData) -> list[tuple[str, str, str]]:
    """Every (child, fk_column, parent) where both sides are tenant-owned.

    Derived from metadata rather than hand-listed, so a new FK is guarded the moment it is
    declared — the same reasoning as the tenancy registry, and the A11 lesson from the
    pilot about lists that rot.
    """
    from mihomes.tenancy.registry import GLOBAL_TABLES, TENANT_TABLES

    links: list[tuple[str, str, str]] = []
    for name, table in metadata.tables.items():
        if name not in TENANT_TABLES:
            continue
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            # A FK to accounts/users cannot drift: there is no parent account_id to differ.
            if parent in GLOBAL_TABLES or parent not in TENANT_TABLES:
                continue
            links.append((name, fk.parent.name, parent))
    return sorted(set(links))


def trigger_ddl_statements(
    metadata: MetaData, only_tables: set[str] | None = None
) -> list[str]:
    """`CREATE TRIGGER` for every guarded link.

    `BEFORE INSERT OR UPDATE OF account_id, <fk>` — the `OF` clause applies only to the
    UPDATE event, so an unrelated column update does not pay for the lookup.

    **`only_tables` exists because a migration must not depend on live model metadata.**
    `0001_pg_baseline` calls this with `Base.metadata`, which keeps growing: SPEC-003's `0007`
    adds `telegram_links`, whose `membership_id` is a guarded link — so `0001` tried to
    `CREATE TRIGGER ... ON telegram_links` six revisions before that table exists, and a
    from-scratch upgrade died with `UndefinedTable`. The same failure `0002_rls` had at G11,
    from the same cause: **a migration is a fixed point in history, and one that imports a
    mutable registry is not.**

    Each later migration emits the guard for the table it creates; `0001` emits it only for the
    tables `0001` itself creates.
    """
    return [
        f"CREATE TRIGGER {trigger_name(child, fk_col)} "
        f"BEFORE INSERT OR UPDATE OF account_id, {fk_col} ON {child} "
        f"FOR EACH ROW EXECUTE FUNCTION "
        f"mihomes_assert_account_matches_parent('{parent}', '{fk_col}')"
        for child, fk_col, parent in parent_links(metadata)
        if only_tables is None or child in only_tables
    ]


def install_drift_guard(metadata: MetaData) -> None:
    """Emit the guard on `create_all`, for Postgres only.

    Bound to the MetaData's `after_create` so it runs once every table exists — a trigger
    cannot reference a parent table that has not been created yet.
    """

    def _emit(target, connection, **kw):
        if connection.dialect.name != "postgresql":
            return  # SQLite tests (the gateway suites) have no PL/pgSQL
        connection.exec_driver_sql(DRIFT_GUARD_FUNCTION)
        for stmt in trigger_ddl_statements(target):
            connection.exec_driver_sql(stmt)

    event.listen(metadata, "after_create", _emit)

    # Dropped with CASCADE so the triggers go with it; otherwise drop_all leaves the
    # function behind and the next create_all's CREATE OR REPLACE silently inherits it.
    event.listen(
        metadata,
        "before_drop",
        DDL(DROP_DRIFT_GUARD_FUNCTION).execute_if(dialect="postgresql"),
    )
