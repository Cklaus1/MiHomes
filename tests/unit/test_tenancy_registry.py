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


#: Global tables that legitimately carry an `account_id`, each with the reason.
#:
#: **The distinction is input versus output.** The general rule below exists because an
#: `account_id` on a global table invites someone to add RLS to it, and RLS on a table read
#: before account context exists returns zero rows and breaks the thing it protects (D3). That
#: reasoning binds when the column is an *input* to visibility — when something decides who may
#: read the row by consulting it.
#:
#: `processed_webhook_events.account_id` is the opposite: it is an **output** of processing. The
#: account is *discovered* by resolving a Stripe customer id (D2) and then recorded, so the
#: ledger can answer "which account did this event apply to" — the first question anyone
#: debugging a billing incident asks. It is never consulted to decide who may read the row, and
#: it is legitimately NULL when an event resolved to no account at all.
#:
#: Declared as data with a reason rather than loosened in the general rule, for the reason U6
#: taught: a correct exemption and a forgotten one are byte-identical in code. A6 asserts this
#: one table has no RLS policy — it says nothing about a *fourth* global table someone adds
#: later, which is exactly what the general rule is prophylactic against.
GLOBAL_TABLES_WITH_ACCOUNT_ID: dict[str, str] = {
    "processed_webhook_events": (
        "SPEC-004 B7 — the account is an OUTPUT of processing, not an input to visibility: a "
        "webhook is verified and recorded before we know whose it is, and this column records "
        "what it resolved to (NULL when nothing). Never read to decide row visibility. A6 "
        "separately asserts the table has no RLS policy."
    ),
}


@pytest.mark.parametrize("table_name", sorted(GLOBAL_TABLES - {"accounts"}))
def test_global_tables_have_no_account_id(table_name):
    """D3 — a tenant policy on these breaks the thing it protects.

    `users` and `sessions` are read before account context exists; `waitlist` ships
    before `accounts` does. An account_id here would invite someone to add RLS to it.

    Exemptions are declared above **with a reason**, never granted by relaxing this assertion —
    a table that is merely absent from the check is indistinguishable from one nobody thought
    about. An exempted table is still asserted here, in the other direction: it must actually
    carry the column it was excused for. **Deliberately not `pytest.skip`** — the build harness
    treats a skipped test as a red gate, and an exemption that makes a test vanish is how a
    carve-out stops being visible.
    """
    table = Base.metadata.tables[table_name]
    if table_name in GLOBAL_TABLES_WITH_ACCOUNT_ID:
        assert "account_id" in table.columns, (
            f"{table_name} is exempted from the no-account_id rule but does not carry the "
            "column — delete the exemption"
        )
        return
    assert "account_id" not in table.columns, (
        f"{table_name} is GLOBAL (D3) and must not carry account_id"
    )


def test_every_account_id_exemption_is_live_and_needed():
    """The exemption list cannot outlive what it excuses.

    Two directions, because each hides a different kind of rot:

    - an entry naming a table that is no longer global (or no longer exists) is a stale carve-out
      that would silently excuse a *future* table reusing the name;
    - an entry for a table that **no longer has** an `account_id` is an exemption excusing
      nothing, which reads as precedent for the next one.

    Without this, the list is the decoration U7 exists to prevent: something that looks like a
    control and enforces nothing.
    """
    for table_name, reason in GLOBAL_TABLES_WITH_ACCOUNT_ID.items():
        assert table_name in GLOBAL_TABLES, (
            f"{table_name} is exempted from the no-account_id rule but is not a global table — "
            "a stale entry would excuse a future table that reuses this name"
        )
        assert table_name in Base.metadata.tables, (
            f"{table_name} is exempted but does not exist in metadata (stale entry?)"
        )
        assert "account_id" in Base.metadata.tables[table_name].columns, (
            f"{table_name} is exempted from the no-account_id rule but has no account_id — "
            "delete the exemption rather than leaving one that excuses nothing"
        )
        assert reason.strip(), f"{table_name}'s exemption must carry a reason"


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

    SPEC-003 adds two:

    - **41** `onboarding_state` (G11/A17) — which steps an account has completed, so a user who
      drops off after step 2 resumes at step 3.
    - **42** `telegram_links` (G16/A32) — sender → membership, keyed so that revoking a membership
      CASCADEs the chat link away.

    Per-person document access adds one:

    - **43** `document_access` — per-person document grants, replacing `documents.staff_visible`
      as the owner-controlled gate. One boolean per document could not say "this is for Ana and
      not for Marco", which an estate needs.

    SPEC-004 adds two, both from the AI usage meter (§4.2, Step 10):

    - **44** `ai_usage_events` — one row per user-initiated AI call, carrying `entry_point` so
      A11 can prove *from the tree* that every dispatch path is metered.
    - **45** `ai_usage_rollups` — the materialized monthly counter `usage()` returns. Materialized
      rather than derived because `archive.py` DELETEs `ai_conversations` (F10), so a derived
      count would reset a customer's usage when they archive.

    - **46** `email_deliveries` — SPEC-005's per-message delivery record (0013, A19). Tenant, so
      it is here; `email_suppressions` (0012) is **not**, because suppression belongs to an
      address and must outlive every account that ever surfaced it.

    - **47** `email_outbox` — the queue `BILLING` §2.4 names and never specifies (0014, D12).
      Tenant because a queued message belongs to the account it is about.

    The webhook ledger is **not** here — it is global, by the carve-out `GLOBAL_TABLES` records.

    Each raise happened in the same commit as its migration. The count exists so that *forgetting*
    to register a table fails loudly, which only works if raising it is a conscious act.
    """
    assert len(TENANT_TABLES) == 49, (
        f"expected 45 tenant-owned tables, registry has {len(TENANT_TABLES)} — "
        "if a table was legitimately added or removed, update this number and say why"
    )
