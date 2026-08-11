"""G6.1 — every table's primary key is an app-side UUIDv7 (SPEC-002 D2).

D2: *"`uuid` PK, UUIDv7, app-side via `mihomes.ids.new_id()`. **No DB-side
default**"*, because `gen_random_uuid()` emits v4 and would destroy the index
locality that is the entire reason to choose v7.

So the type alone is not the requirement. A conversion that sets `PGUUID` but keeps
autoincrement semantics, or adds `server_default=gen_random_uuid()`, satisfies a
type check and breaks the decision. All three properties are asserted here.

Also the reason this group had to run before G15: `Base.metadata.create_all()` on
Postgres fails with `DatatypeMismatch` while any FK points from a `uuid` column at
an `integer` primary key.
"""

import uuid

import pytest
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from mihomes.models import Base
from mihomes.tenancy.registry import GLOBAL_TABLES, TENANT_TABLES

ALL_TABLES = sorted(TENANT_TABLES | GLOBAL_TABLES)

# Tables whose primary key is NATURAL rather than surrogate. D2's "uuid PK" governs
# surrogate ids — a row identified by what it *is* does not need one.
#
#   configurations   PK is (account_id, key). The key alone was insufficient under
#                    multitenancy: two accounts must each be able to set
#                    `ai.provider`. account_id leads, so the PK index doubles as the
#                    tenant index.
NATURAL_KEY_TABLES = frozenset({"configurations"})


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_primary_key_is_uuid(table_name):
    """Parametrized so a failure names the table rather than "something somewhere"."""
    if table_name in NATURAL_KEY_TABLES:
        pytest.skip(f"{table_name} has a natural primary key — see NATURAL_KEY_TABLES")

    table = Base.metadata.tables[table_name]
    pks = list(table.primary_key.columns)
    assert pks, f"{table_name} has no primary key"

    for col in pks:
        # Association tables have composite PKs of two FKs, which are covered by
        # test_foreign_keys_match_their_target below.
        if col.foreign_keys:
            continue
        assert isinstance(col.type, PGUUID), (
            f"{table_name}.{col.name} is {col.type!r}, expected PGUUID (D2)"
        )


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_no_db_side_default_on_the_primary_key(table_name):
    """D2 — **no DB-side default.**

    `gen_random_uuid()` emits v4. Mixing versions in one column destroys v7's
    time-ordering, which is what gives insert locality — and losing that silently
    is worse than a visible error, because nothing fails until the index bloats.
    """
    table = Base.metadata.tables[table_name]
    for col in table.primary_key.columns:
        if col.foreign_keys or table_name in NATURAL_KEY_TABLES:
            continue
        assert col.server_default is None, (
            f"{table_name}.{col.name} has a server_default — D2 forbids it"
        )
        assert col.autoincrement is not True, (
            f"{table_name}.{col.name} still declares autoincrement"
        )


@pytest.mark.parametrize("table_name", ALL_TABLES)
def test_primary_key_default_generates_v7(table_name):
    """The app-side default must actually produce a version-7 UUID.

    Asserts behaviour, not identity: `new_id` binds to `uuid.uuid7` on 3.14+, and
    comparing function objects across import paths is brittle (learned in SPEC-001).
    """
    if table_name in NATURAL_KEY_TABLES:
        pytest.skip(f"{table_name} has a natural primary key — see NATURAL_KEY_TABLES")

    table = Base.metadata.tables[table_name]
    for col in table.primary_key.columns:
        if col.foreign_keys:
            continue
        assert col.default is not None, f"{table_name}.{col.name} has no app-side default"

        generated = col.default.arg(None)
        assert isinstance(generated, uuid.UUID)
        assert generated.version == 7, (
            f"{table_name}.{col.name} default produced v{generated.version}, not v7"
        )


def test_no_foreign_key_type_mismatches():
    """Every FK column's type matches the primary key it references.

    This is the gate that G15 needs: `create_all()` on Postgres raises
    `DatatypeMismatch` for a `uuid` column referencing an `integer` PK, which is how
    the one mismatch introduced in G1 surfaced. SQLite is permissive and would have
    hidden it until deploy.
    """
    mismatches = []
    for name, table in sorted(Base.metadata.tables.items()):
        for col in table.columns:
            for fk in col.foreign_keys:
                target = fk.column
                if type(col.type).__name__ != type(target.type).__name__:
                    mismatches.append(
                        f"{name}.{col.name} ({type(col.type).__name__}) -> "
                        f"{fk.target_fullname} ({type(target.type).__name__})"
                    )

    assert mismatches == [], "FK type mismatches:\n  " + "\n  ".join(mismatches)


def test_metadata_creates_on_postgres_shape():
    """Sanity: no table mixes a UUID pk with integer FK children.

    A cheap structural stand-in for `create_all` against a live server, so the whole
    suite does not need a database to catch a regression here.
    """
    for name, table in sorted(Base.metadata.tables.items()):
        for col in table.columns:
            if col.name.endswith("_id") and col.foreign_keys:
                target = next(iter(col.foreign_keys)).column
                assert type(col.type).__name__ == type(target.type).__name__, (
                    f"{name}.{col.name} would fail CREATE TABLE on Postgres"
                )
