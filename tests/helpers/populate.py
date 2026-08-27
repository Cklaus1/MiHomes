"""A populated account: one row in **every** `TenantOwned` table, derived from the schema.

§9 asks for this and says why in one line: *"A purge test against an account with three rows
proves nothing."* A28 claims the purge reaches every account-scoped table; that claim is only
meaningful if every table had something in it to reach.

## Derived, not hand-written

A hand-maintained list of 49 seeds is exactly the artifact G-purge exists to prevent. It rots the
moment a model lands, and it rots by **under-seeding** — which makes A28 pass on tables that were
never populated, in the specific direction where nothing looks wrong.

So the rows are synthesized from `Base.metadata`: tables in `sorted_tables` order (topological, so
a foreign key's parent already exists), every NOT NULL column filled from its type, and every FK
resolved to an id inserted a moment earlier. A new model gets seeded automatically, and
`assert_fully_populated` fails if any table ends up empty.

Raw Core inserts rather than the service layer, deliberately: services enforce plan limits, write
audit rows, and refuse combinations the schema permits. This fixture's job is to fill tables, not
to be realistic — the realism lives in the tests that assert what the purge did to it.
"""

from __future__ import annotations

import datetime
import json
import uuid

import sqlalchemy as sa

from mihomes.models import Base
from mihomes.tenancy.registry import TENANT_TABLES

__all__ = ["assert_fully_populated", "populate_account", "seeded_tables"]

#: Columns whose value must satisfy a CHECK, an enum, or code that reads it back. Everything
#: else is synthesized from its SQL type.
#:
#: **Each entry is a value the schema will not accept from the generic path**, not a preference:
#: a `VARCHAR(10)` holding `'warranty'` is an enum in Python and free text in Postgres, and a
#: generic string would insert successfully and then fail on read.
EXPLICIT_VALUES = {
    # A seeded deletion request must be **already resolved**, or it is a *live* request:
    # `request_deletion` is idempotent and would hand the fixture's synthetic row back instead
    # of creating one, so the state-machine tests would assert against a row nobody made.
    # Found by `test_request_deletes_nothing` reading `purge_after == requested_at` — both the
    # synthesized 2026-01-01.
    ("account_deletion_requests", "cancelled_at"): datetime.datetime(
        2026, 1, 2, 12, 0, tzinfo=datetime.UTC
    ),
    ("documents", "document_type"): "warranty",
    ("insurance_policies", "insurance_type"): "homeowners",
    ("staff", "role"): "housekeeper",
    ("memberships", "role"): "owner",
    ("invites", "role"): "staff",
    ("properties", "property_type"): "primary",
    ("assets", "asset_type"): "equipment",
    ("budgets", "period"): "monthly",
    ("recurring_expenses", "frequency"): "monthly",
    ("task_schedules", "frequency"): "weekly",
    ("email_outbox", "klass"): "transactional",
    ("email_outbox", "context"): "{}",
    ("staff_pto_requests", "dates"): json.dumps(["2026-06-01"]),
    ("notes", "entity_type"): "property",
    ("tag_assignments", "entity_type"): "property",
}

#: Tables that reference a **global** table. Their FK cannot be satisfied from tenant rows, so
#: the caller supplies the id (a user, today).
GLOBAL_FK_COLUMNS = {("memberships", "user_id")}


def _synthesize(table_name: str, column, ids: dict, marker: str, user_id, account_id):
    """A value for one NOT NULL column, by name, FK, then type."""
    explicit = EXPLICIT_VALUES.get((table_name, column.name))
    if explicit is not None:
        return explicit

    if (table_name, column.name) == ("documents", "file_path"):
        # A **real storage key**, `{account_id}/{category}/{opaque}`, not a synthetic string.
        # `is_storage_key` requires the tenant prefix, so a generic value makes the purge skip
        # the document — and A10 then asserts against zero storage deletions and passes for the
        # wrong reason. Found exactly that way.
        from mihomes.storage import build_key

        return build_key(account_id, "documents", "seed.pdf")

    if (table_name, column.name) in GLOBAL_FK_COLUMNS:
        return user_id

    for fk in column.foreign_keys:
        parent = fk.column.table.name
        if parent in ids:
            return ids[parent]
        # A forward reference the topological order did not resolve — a self-reference, or a
        # parent this seeder skipped. Surfaced rather than papered over with a random uuid,
        # which would insert and then violate the FK.
        raise RuntimeError(
            f"{table_name}.{column.name} references {parent}, which has no seeded row"
        )

    # **The Python type the column binds, not its SQL name.** `Money` reports as `Integer`
    # (it stores cents) but its bind path runs the value through `Decimal(str(value))`, so an
    # int-shaped string raises `decimal.ConversionSyntax`. Dispatching on the SQL type name
    # alone gets this exactly backwards, which is how the first version of this failed.
    try:
        if column.type.python_type is float:
            return 1.0
    except NotImplementedError:
        pass

    kind = type(column.type).__name__.lower()
    if "money" in kind:
        return 1.0
    if "uuid" in kind:
        return uuid.uuid4()
    if "datetime" in kind:
        return datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)
    if "date" in kind:
        return datetime.date(2026, 1, 1)
    if "time" in kind:
        return datetime.time(12, 0)
    if "boolean" in kind:
        return False
    if "float" in kind or "numeric" in kind:
        return 1.0
    if "integer" in kind or "bigint" in kind:
        # Distinct per column so a unique constraint on one does not collide.
        return abs(hash((table_name, column.name, marker))) % 1_000_000
    if "json" in kind:
        return "{}"
    length = getattr(column.type, "length", None) or 40
    return f"{column.name}-{marker}"[:length]


def seeded_tables() -> list[str]:
    """Tenant tables in dependency order — parents before children."""
    return [t.name for t in Base.metadata.sorted_tables if t.name in TENANT_TABLES]


def populate_account(connection, account_id, user_id) -> dict[str, uuid.UUID]:
    """Insert one row into every tenant table for `account_id`. Returns table -> row id.

    Takes a `Connection` (`session.connection()` is one), and **does not commit**. The caller
    owns the transaction: `test_deletion`'s fixture passes the test session's own connection so
    everything rolls back with the test.

    Committing here is what the first version did, on a second connection — and it deadlocked
    the suite. The `session` fixture holds an open transaction, so its uncommitted INSERT into
    `account_deletion_requests` blocked the seeder's teardown DELETE against the same table, and
    pytest hung with no output at all.
    """
    marker = uuid.uuid4().hex[:8]
    ids: dict[str, uuid.UUID] = {}

    for table_name in seeded_tables():
        table = Base.metadata.tables[table_name]
        values = {"account_id": account_id}

        row_id = uuid.uuid4()
        if "id" in table.columns:
            values["id"] = row_id

        for column in table.columns:
            if column.name in values:
                continue
            explicit = EXPLICIT_VALUES.get((table_name, column.name))
            if explicit is not None:
                # An explicit value wins even on a NULLABLE column — that is how a seeded
                # deletion request is marked resolved rather than left live.
                values[column.name] = explicit
                continue
            if column.nullable:
                continue
            if column.default is not None or column.server_default is not None:
                continue
            values[column.name] = _synthesize(
                table_name, column, ids, marker, user_id, account_id
            )

        connection.execute(sa.insert(table).values(**values))
        ids[table_name] = row_id

    return ids


def assert_fully_populated(connection, account_id) -> None:
    """Every tenant table holds at least one row for this account.

    **The assertion that keeps A28 from being vacuous.** Without it a seeder that silently
    skipped half the schema would leave the purge test asserting that empty tables are empty —
    which is true, and proves nothing about a purge.
    """
    empty = []
    for table_name in seeded_tables():
        table = Base.metadata.tables[table_name]
        count = connection.execute(
            sa.select(sa.func.count())
            .select_from(table)
            .where(table.c.account_id == account_id)
        ).scalar()
        if not count:
            empty.append(table_name)

    assert not empty, (
        "the populated-account fixture left these tables empty, so any purge assertion "
        f"about them is vacuous: {empty}"
    )
