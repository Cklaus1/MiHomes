"""A1 — every non-global table is account-scoped, and the registry knows it all.

This is the test the whole tenancy design leans on: A21 iterates the same registry,
so a table missing from it escapes **both** the isolation test and RLS generation
while A21 still reports green.

So the assertions here are **positive and exhaustive**. They compare the registry
against live metadata in both directions rather than checking that "the models we
know about" carry a column — the pilot's A11 lesson was that a sampled assertion
rots the moment someone adds a table.
"""

import pytest

from mihomes.models import Base, TenantOwned
from mihomes.tenancy.registry import (
    ASSOCIATION_TABLES,
    GLOBAL_TABLES,
    TENANT_TABLES,
    TEST_ONLY_TABLES,
    check_registry,
    tenant_models,
)


def test_all_models_tenant_owned():
    """A1 — every non-global model subclasses TenantOwned (or carries account_id).

    The registry is the authority and `check_registry()` reconciles it against
    metadata: unclassified tables, stale entries, and registered-but-missing
    `account_id` are all reported.
    """
    problems = check_registry()
    assert problems == [], "registry inconsistent:\n  " + "\n  ".join(problems)


def test_every_metadata_table_is_classified():
    """No table may be neither tenant-owned nor global.

    An unclassified table is the dangerous state: it gets no RLS policy and no
    isolation coverage, and nothing fails. This asserts the partition is total.
    """
    live = {
        name for name in Base.metadata.tables
        if not name.startswith("alembic_version") and name not in TEST_ONLY_TABLES
    }
    assert live == TENANT_TABLES | GLOBAL_TABLES, (
        f"unclassified: {sorted(live - TENANT_TABLES - GLOBAL_TABLES)}; "
        f"stale: {sorted((TENANT_TABLES | GLOBAL_TABLES) - live)}"
    )


def test_tenant_and_global_do_not_overlap():
    """A table cannot be both. Overlap would mean the two lists disagree silently."""
    assert not (TENANT_TABLES & GLOBAL_TABLES)


@pytest.mark.parametrize("table_name", sorted(TENANT_TABLES))
def test_each_tenant_table_has_account_id(table_name):
    """Parametrized so a failure names the offending table, not just "something".

    Covers the Core association tables too — they have no declarative class, so a
    mixin-based check would skip them entirely.
    """
    table = Base.metadata.tables[table_name]
    assert "account_id" in table.columns, f"{table_name} has no account_id"

    col = table.c.account_id
    assert col.nullable is False, f"{table_name}.account_id must be NOT NULL"
    # A leading primary-key position counts as indexed: Postgres backs every primary key with a
    # unique index, so `WHERE account_id = ?` is served by it. `onboarding_state` (SPEC-003 A17)
    # is keyed *on* `accounts.id`, so demanding a second explicit index would add a redundant
    # one — and `alembic check` would then report drift, because the model declares none.
    #
    # The invariant is unchanged: every tenant query filtering on `account_id` must hit an index.
    # This widens *how* that can be satisfied, not *whether* it must be.
    pk_columns = list(table.primary_key.columns)
    indexed_by_pk = bool(pk_columns) and pk_columns[0].name == "account_id"
    assert (
        col.index is True
        or indexed_by_pk
        or any(list(ix.columns)[0].name == "account_id" for ix in table.indexes)
    ), f"{table_name}.account_id must be indexed — every tenant query filters on it"

    fks = {fk.target_fullname for fk in col.foreign_keys}
    assert "accounts.id" in fks, f"{table_name}.account_id must FK to accounts.id"


@pytest.mark.parametrize("table_name", sorted(GLOBAL_TABLES - {"accounts"}))
def test_global_tables_have_no_account_id(table_name):
    """D3 — a tenant policy on these breaks the thing it protects.

    `users` and `sessions` are read before account context exists; `waitlist` ships
    before `accounts` does. An account_id here would invite someone to add RLS to it.
    """
    table = Base.metadata.tables[table_name]
    assert "account_id" not in table.columns, (
        f"{table_name} is GLOBAL (D3) and must not carry account_id"
    )


def test_accounts_is_the_root_not_a_tenant():
    """`accounts` has no account_id — it is what account_id points at."""
    assert "account_id" not in Base.metadata.tables["accounts"].columns
    assert "accounts" not in TENANT_TABLES


def test_association_tables_are_registered_despite_having_no_class():
    """**The leak this registry exists to close.**

    `staff_properties` and `vendor_properties` are Core `Table` objects. A
    `@declared_attr` mixin cannot reach them, so a registry derived from
    `TenantOwned.__subclasses__()` omits them — no account_id, no RLS policy, no
    A21 coverage, and A21 still green. They must be in TENANT_TABLES explicitly.
    """
    mapped = {m.class_.__tablename__ for m in Base.registry.mappers}

    for name in ASSOCIATION_TABLES:
        assert name not in mapped, (
            f"{name} is expected to be a Core Table; if it gained a declarative "
            "class, simplify the registry rather than keeping the special case"
        )
        assert name in TENANT_TABLES, f"{name} must be registered explicitly"
        assert "account_id" in Base.metadata.tables[name].columns


def test_registry_is_not_merely_the_mixin_subclasses():
    """Guards the design, not just the data.

    If someone "simplifies" TENANT_TABLES to `__subclasses__()`, this fails —
    because the derived set cannot contain the two Core tables.
    """
    derived = {c.__tablename__ for c in TenantOwned.__subclasses__()}
    assert TENANT_TABLES - derived, (
        "TENANT_TABLES must be broader than the mixin's subclasses: the Core "
        "association tables can never appear in __subclasses__()"
    )
    assert ASSOCIATION_TABLES.isdisjoint(derived)


def test_tenant_models_helper_covers_the_mapped_ones():
    """`tenant_models()` is for ORM-level work; it excludes the classless tables."""
    names = {m.__tablename__ for m in tenant_models()}
    assert names == TENANT_TABLES - ASSOCIATION_TABLES


def test_registry_size_is_asserted_explicitly():
    """A count, so silently dropping a table from the registry fails loudly.

    40, not SPEC-002's 37: the spec predates Phase 0 and counts only the domain
    tables, omitting `invites`, `memberships` and `membership_property_scopes`,
    which its own Step 1 adds.

    **41 as of SPEC-003 G11:** `onboarding_state` (A17) records which onboarding steps an account
    has completed, so a user who drops off after step 2 resumes at step 3. Raised deliberately in
    the same commit as migration `0004` — the count exists so that *forgetting* to register a
    table fails loudly, which only works if raising it is a conscious act.
    """
    assert len(TENANT_TABLES) == 41, (
        f"expected 41 tenant-owned tables, registry has {len(TENANT_TABLES)} — "
        "if a table was legitimately added or removed, update this number and say why"
    )
