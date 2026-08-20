"""G3 · §6 Step 3 — composite indexes lead with `account_id`.

Under RLS every query against a tenant table carries an implicit
`account_id = current_setting('app.current_account')` predicate, so an index that does
not lead with `account_id` makes Postgres recheck the tenant filter per row.

**Why this asserts metadata rather than running `EXPLAIN`.** The spec's verify clause is
*"`EXPLAIN` on representative queries shows an index scan, not a sequential scan"*, but a
sequential scan is the *correct* plan on a table with a handful of rows — the planner
ignores an index it would be slower to use. An EXPLAIN gate would therefore fail (or
pass) for reasons unrelated to whether the index is right, and seeding enough rows per
table to move the planner would make this a slow integration test that still only
proves the planner's cost model. Asserting index *existence and column order* is
deterministic and is the property Step 3 actually asks for. `test_indexes_exist_in_postgres`
below closes the gap between "declared in metadata" and "created in the database".
"""

from sqlalchemy import inspect

from mihomes.models import Base
from mihomes.tenancy.registry import TENANT_TABLES

# Indexes that deliberately do NOT lead with account_id. Every entry needs a reason;
# an unexplained addition here is how the Step 3 guarantee would rot.
#
# G5 retired the 16 slug/name entries that used to live here: `UNIQUE (account_id, slug)`
# (and `(account_id, name)` for tags) produces an index leading with account_id on its own,
# so `index=True` came off `SlugMixin.slug` in the same pass. Only the permanent exception
# remains — which is the point of `test_every_declared_exception_still_exists`: the list
# could not have quietly kept 16 stale entries.
EXPECTED_NON_LEADING = {
    "ix_invites_token_hash": (
        "An invite is accepted by presenting this token before the recipient belongs "
        "to any account, so the lookup cannot supply one. A composite index would also "
        "let two accounts mint the same token hash."
    ),
    "ix_telegram_links_lookup": (
        "SPEC-003 G16. The bot resolves a sender before it knows which account they belong to — "
        "that resolution IS how the account is discovered (TELEGRAM_PRD:129, "
        "telegram_user_id -> membership -> account). Leading with account_id would leave the "
        "only query this table exists to serve unindexed. Isolation is unaffected: the row still "
        "carries account_id, RLS still applies, and UNIQUE (account_id, telegram_user_id) keeps "
        "one link per sender per account."
    ),
}


def _tenant_indexes():
    for name, table in Base.metadata.tables.items():
        if name not in TENANT_TABLES:
            continue
        for idx in table.indexes:
            yield name, idx.name, [c.name for c in idx.columns]


def test_composite_indexes_lead_with_account_id():
    offenders = [
        f"{table}.{idx} = ({', '.join(cols)})"
        for table, idx, cols in _tenant_indexes()
        if cols[0] != "account_id" and idx not in EXPECTED_NON_LEADING
    ]
    assert not offenders, (
        "these tenant-table indexes do not lead with account_id and are not a declared "
        "exception:\n  " + "\n  ".join(sorted(offenders))
    )


def test_every_declared_exception_still_exists():
    """A stale allow-list entry is as bad as a missing index.

    Without this, G5 could convert `ix_properties_slug` and leave the entry behind, and
    the next non-leading index named `ix_properties_slug` would be waved through.
    """
    actual = {idx for _, idx, _ in _tenant_indexes()}
    stale = sorted(set(EXPECTED_NON_LEADING) - actual)
    assert not stale, (
        "these indexes are listed as non-leading exceptions but no longer exist — "
        f"remove them from EXPECTED_NON_LEADING: {stale}"
    )


def test_preexisting_table_args_survived_the_merge():
    """G3.2 — merging into an existing `__table_args__` must not replace it.

    Four models declared `__table_args__` before SPEC-002 (the spec claims one). G3 adds
    indexes to those same tuples, and a tuple assignment rather than an extension would
    drop these silently — nothing else in the suite asserts them by name.
    """
    expected = {
        "budgets": "uq_budget_property_category_period",
        "event_guests": "uq_event_guest",
        "tag_assignments": "uq_tag_assignment",
    }
    for table_name, constraint_name in expected.items():
        table = Base.metadata.tables[table_name]
        names = {c.name for c in table.constraints}
        assert constraint_name in names, (
            f"{table_name} lost {constraint_name} — the G3 merge replaced "
            f"__table_args__ instead of extending it. Present: {sorted(names)}"
        )
    # note.py's pre-existing index is *modified* by G3 rather than merged alongside:
    # account_id becomes its leading column.
    note_idx = {i.name: [c.name for c in i.columns] for i in Base.metadata.tables["notes"].indexes}
    assert note_idx["ix_note_entity"] == ["account_id", "entity_type", "entity_id"]


def test_indexes_exist_in_postgres(_pg_engine):
    """Declared in metadata is not the same as created in the database.

    `create_all` builds the schema these tests describe, so this closes the loop that
    metadata-only assertions leave open.
    """
    inspector = inspect(_pg_engine)
    for table in sorted(TENANT_TABLES):
        declared = {i.name for i in Base.metadata.tables[table].indexes}
        if not declared:
            continue
        actual = {i["name"] for i in inspector.get_indexes(table)}
        missing = declared - actual
        assert not missing, f"{table}: declared but not created in Postgres: {sorted(missing)}"
