"""G4 · §6 Step 4 — a child's ``account_id`` cannot diverge from its parent's.

Three properties, because the first alone would pass against a schema with no guard at
all and the second is the one a future reader is most likely to "fix" into a bug:

1. a cross-tenant parent is **rejected by the database**  (the spec's verify clause)
2. a NULL optional parent is **accepted**                 (MATCH SIMPLE, deliberate)
3. the trigger **exists on every guarded child**          (so a new FK cannot slip through)

See ``mihomes/tenancy/drift_guard.py`` for why this is a trigger rather than the composite
FK Step 4 suggests, and for the four polymorphic tables it deliberately does not cover.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mihomes.models import Base
from mihomes.tenancy.drift_guard import parent_links, trigger_name


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
    # Every NOT NULL column without a server default, enumerated from
    # information_schema rather than discovered one failure at a time. Enum labels are
    # the Python member NAMES, not their values — SQLAlchemy's Enum() default — so the
    # DB type accepts 'PRIMARY', not 'primary'.
    conn.execute(
        text(
            "INSERT INTO properties "
            "(id, account_id, name, slug, property_type, status, currency, occupied) "
            "VALUES (:id, :account_id, :name, :slug, 'PRIMARY', 'OPEN', 'USD', false)"
        ),
        {"id": property_id, "account_id": account_id, "name": name, "slug": name},
    )
    return property_id


def test_child_account_mismatch_rejected(_pg_engine):
    """A space in account A pointing at a property owned by account B must not commit.

    Raw SQL on purpose: this asserts the *database* rejects it. Going through the ORM
    would let the G8.3 stamp listener supply a consistent account_id and prove nothing
    about the schema.
    """
    suffix = uuid.uuid4().hex[:8]
    with _pg_engine.begin() as conn:
        acct_a = _make_account(conn, f"drift-a-{suffix}")
        acct_b = _make_account(conn, f"drift-b-{suffix}")
        prop_b = _make_property(conn, acct_b, f"b-manor-{suffix}")

        with pytest.raises(IntegrityError) as exc:
            conn.execute(
                text(
                    "INSERT INTO spaces (id, account_id, property_id, name, slug, created_at) "
                    "VALUES (:id, :account_id, :property_id, 'Study', :slug, now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "account_id": acct_a,      # account A ...
                    "property_id": prop_b,     # ... pointing at account B's property
                    "slug": f"study-{suffix}",
                },
            )
        assert "tenant drift" in str(exc.value), (
            f"rejected, but not by the drift guard: {exc.value}"
        )
        conn.rollback()


def test_null_optional_parent_accepted(_pg_engine):
    """MATCH SIMPLE: an optional parent left NULL has nothing to diverge from.

    `spaces.zone_id` is nullable. If the guard's `IS NULL` early return is ever removed,
    every optional relationship in the schema breaks — and it would look like a
    tightening rather than a regression, which is why this is a named test.
    """
    suffix = uuid.uuid4().hex[:8]
    with _pg_engine.begin() as conn:
        acct = _make_account(conn, f"drift-null-{suffix}")
        prop = _make_property(conn, acct, f"null-manor-{suffix}")
        conn.execute(
            text(
                "INSERT INTO spaces (id, account_id, property_id, zone_id, name, slug, created_at) "
                "VALUES (:id, :account_id, :property_id, NULL, 'Hall', :slug, now())"
            ),
            {
                "id": uuid.uuid4(),
                "account_id": acct,
                "property_id": prop,
                "slug": f"hall-{suffix}",
            },
        )
        conn.rollback()


def test_trigger_present_on_every_guarded_child(_pg_engine):
    """Enumerated from the same metadata the guard is generated from.

    So a 28th child table, or a new FK on an existing one, fails here rather than being
    silently unguarded — the failure mode that made G6.1's gate go green over a broken
    web layer.
    """
    links = parent_links(Base.metadata)
    assert links, "no guarded links found — the audit should find 52"

    with _pg_engine.connect() as conn:
        actual = {
            row[0]
            for row in conn.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
        }

    missing = sorted(
        trigger_name(child, fk_col)
        for child, fk_col, _ in links
        if trigger_name(child, fk_col) not in actual
    )
    assert not missing, f"drift-guard triggers not created: {missing}"


def test_polymorphic_tables_are_documented_as_uncovered():
    """The spec forbids skipping the polymorphic four *silently*.

    This asserts the exclusion stays deliberate: if someone adds a real FK on `entity_id`
    to one of these, it joins the guarded set and this test tells them to update the note
    in `drift_guard.py` rather than leaving a stale "not covered" claim behind.
    """
    polymorphic = {"notes", "documents", "audit_log", "tag_assignments"}
    guarded = {child for child, _, _ in parent_links(Base.metadata)}
    for table in polymorphic:
        cols = {c.name for c in Base.metadata.tables[table].columns}
        assert {"entity_type", "entity_id"} <= cols, f"{table} lost its polymorphic columns"
        has_entity_fk = any(
            fk.parent.name == "entity_id"
            for fk in Base.metadata.tables[table].foreign_keys
        )
        assert not has_entity_fk, (
            f"{table}.entity_id gained a ForeignKey — it can now be drift-guarded, so "
            "update the 'not covered' note in mihomes/tenancy/drift_guard.py"
        )
    # tag_assignments is in BOTH buckets: its tag_id side IS guarded.
    assert "tag_assignments" in guarded, (
        "tag_assignments.tag_id is a real FK and must still be drift-guarded even though "
        "its entity_id side cannot be"
    )
