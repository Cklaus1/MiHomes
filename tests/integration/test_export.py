"""G7 · §6 Step 7 — data export (A6, A26, A27).

Half of the GA gate at `SAAS_PRD:193`. The other half is Step 8's deletion, and Step 7 comes
first because deletion has to offer the export (`PRICING` §4.4).

**A27 is the derived gate (G-export)**: the bundle's tables are compared against the registry at
test time, never against a list written here. §9 states the discipline directly — *"a test that
lists tables by hand passes forever while the feature silently rots"* — and F4 is what that looks
like in production: `csv_io.export_csv` covers 5 of 28 model modules and has looked like a working
exporter the whole time.

**These tests deliberately run as `postgres`, a superuser**, via the shared `session` fixture —
and that is why A6 caught the real bug here. A superuser bypasses RLS unconditionally, even under
`FORCE ROW LEVEL SECURITY`, so the *only* thing filtering an export in this environment is the
ORM's `with_loader_criteria`. That is precisely the layer D14 names. Switching to `app_engine`
would let RLS mask an ORM-layer hole: the export would look correct in tests and be wrong in any
code path where the ORM filter is what applies.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest
from sqlalchemy import text

from mihomes.services.privacy import build_export
from mihomes.tenancy.context import account_context
from mihomes.tenancy.registry import TENANT_TABLES


def _seed_one_row_everywhere(session, account_id):
    """A property and a vendor — enough that the bundle is not trivially empty.

    §9 asks for "one account with at least one row in every `TenantOwned` table", and that is
    the right ambition; it is also 49 tables with a dependency graph. What A27 needs is that the
    *enumeration* is complete, which is asserted against the registry rather than against seeded
    data — so a partial seed cannot make it pass vacuously. A26 needs real rows in the OTHER
    account, and that is what `_seed_other_account` provides.
    """
    from mihomes.services import property as prop_svc
    from mihomes.services import vendor as vendor_svc

    prop = prop_svc.create_property(session, "Export Manor")
    vendor_svc.create_vendor(session, "Export Vendor", service_categories=["Pest"])
    session.flush()
    return prop


def test_covers_all_tenant_tables(session, account_a):
    """**A27** — the export enumerates every `TenantOwned` table, discovered at test time.

    Set equality against `TENANT_TABLES`, not "the tables I expected are present": containment
    would pass while the newest model was missing, which is the exact failure mode F4 documents.

    `TENANT_TABLES` rather than `tenant_models()` matters here. The latter walks
    `Base.registry.mappers`, which cannot see `staff_properties` and `vendor_properties` — two
    real account-scoped tables with no declarative class. An ORM-only export would omit them and
    an ORM-only test would report green over the omission.
    """
    with account_context(account_a):
        _seed_one_row_everywhere(session, account_a)
        bundle = build_export(session, account_a)

    assert set(bundle.tables) == set(TENANT_TABLES), (
        "the export must cover exactly the account-scoped tables:\n"
        f"  missing:   {sorted(set(TENANT_TABLES) - set(bundle.tables))}\n"
        f"  unexpected:{sorted(set(bundle.tables) - set(TENANT_TABLES))}"
    )


def test_the_association_tables_are_included(session, account_a):
    """The two tables an ORM-only sweep silently drops.

    Called out separately from A27 so a regression names them: `staff_properties` and
    `vendor_properties` carry `account_id` and have no mapped class, and the registry exists
    because a `__subclasses__()`-based check once reported green over exactly this gap.
    """
    with account_context(account_a):
        bundle = build_export(session, account_a)

    for table in ("staff_properties", "vendor_properties"):
        assert table in bundle.tables, f"{table} is account-scoped and must be exported"


@contextlib.contextmanager
def _foreign_association_row(engine, account_id):
    """A `staff_properties` row under a *different* account, via the service layer.

    The association tables are the one place with **no** fallback: `with_loader_criteria` binds
    to a mapped class and these have none, and the suite runs as superuser so RLS does not cover
    for it either. Without this seed, deleting the explicit `.where(account_id == ...)` from
    `build_export` leaves every test green — the same shape as the bug A6 caught, in the place
    where nothing else would catch it.

    Built through `create_staff` rather than raw SQL: three successive attempts at hand-written
    INSERTs each hit a different column the model fills in Python (`role` NOT NULL with a
    client-side default, its VALUE not its NAME, and a status enum behind that). The services
    know the schema; a test re-deriving it is guessing.

    Its own session on its own connection, committed, so the row is real data belonging to
    another tenant rather than something the test's session could see anyway.
    """
    from sqlalchemy.orm import Session as SASession

    from mihomes.models.staff import StaffRole
    from mihomes.services import property as prop_svc
    from mihomes.services import staff as staff_svc

    marker = f"assoc-{uuid.uuid4().hex[:8]}"
    with account_context(account_id), SASession(engine) as other:
        prop = prop_svc.create_property(other, f"Manor {marker}")
        staff = staff_svc.create_staff(
            other,
            f"Neighbour {marker}",
            role=StaffRole.HOUSEKEEPER,
            property_id_or_slug=prop.slug,
        )
        other.commit()
        staff_id, property_id = staff.id, prop.id

    try:
        yield staff_id
    finally:
        # Committed rows do not roll back with the test's session. Children first.
        #
        # **The audit rows too.** `create_property` and `create_staff` call `record_change`,
        # and `test_archive`'s `get_stats` counts `audit_log` across the whole database — so
        # two stray entries made it assert `4 == 2` from a module away. Going through the
        # service layer is right (the schema knows itself better than a hand-written INSERT),
        # and the cost is cleaning up what the services legitimately wrote.
        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM audit_log WHERE account_id = :a AND entity_id IN (:s, :p)"),
                {"a": account_id, "s": str(staff_id), "p": str(property_id)},
            )
            conn.execute(
                text("DELETE FROM staff_properties WHERE staff_id = :s"), {"s": staff_id}
            )
            conn.execute(text("DELETE FROM staff WHERE id = :s"), {"s": staff_id})
            conn.execute(text("DELETE FROM properties WHERE id = :p"), {"p": property_id})
            conn.commit()


def test_association_tables_are_filtered_by_account(session, account_a, account_b, _pg_engine):
    """The blind spot, asserted rather than described.

    `staff_properties` and `vendor_properties` carry `account_id` and have **no declarative
    class**, so `tenancy/session.py`'s ORM filter cannot reach them — its own docstring calls
    this out. `build_export` filters them explicitly, and this is what proves that filter is
    doing something: seed a foreign row, and assert the export does not contain it.
    """
    with _foreign_association_row(_pg_engine, account_b) as foreign_staff_id, \
            account_context(account_a):
        bundle = build_export(session, account_a)

    rows = bundle.tables["staff_properties"]
    assert all(row["account_id"] == str(account_a) for row in rows), rows
    assert str(foreign_staff_id) not in json.dumps(rows, default=str), (
        "account B's staff_properties row appeared in account A's export — the association "
        "tables have no ORM filter, so this one is explicit or it is nothing"
    )


def test_every_table_appears_even_when_empty(session, account_a):
    """An absent key and an empty list mean different things.

    Only the second can be told apart from a table the export forgot — which is what makes
    A27's set equality meaningful rather than a statement about how much data happened to exist.
    """
    with account_context(account_a):
        bundle = build_export(session, account_a)

    empty = [name for name, rows in bundle.tables.items() if rows == []]
    assert empty, "expected at least one empty table in a bare account"
    assert all(isinstance(rows, list) for rows in bundle.tables.values())


@contextlib.contextmanager
def _other_accounts_property(engine, account_id):
    """Commit a distinctive row under a *different* account, and remove it afterwards.

    Raw SQL on its own connection, because the point is to write data this account's session
    can never legitimately see — going through the scoped session would either bind the wrong
    tenant or be filtered on the way in, and prove nothing either way.

    **Committed, therefore cleaned up.** The shared `session` fixture rolls back, so most tests
    need no teardown; a committed row does not vanish, and this one broke three unrelated tests
    (`test_archive`'s `get_stats` counts rows across the whole database, and `test_trial`'s
    fixtures assume a known estate). Passing alone and failing in the suite, one module later —
    the second time this run.
    """
    property_id = uuid.uuid4()
    marker = f"NEIGHBOUR-{uuid.uuid4().hex[:8]}"
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO properties (id, account_id, name, slug, property_type, "
                "status, currency, occupied) "
                "VALUES (:id, :acct, :name, :slug, 'PRIMARY', 'OPEN', 'USD', false)"
            ),
            {
                "id": property_id,
                "acct": account_id,
                "name": marker,
                "slug": marker.lower(),
            },
        )
        conn.commit()
    try:
        yield marker
    finally:
        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM properties WHERE id = :id"), {"id": property_id}
            )
            conn.commit()


def test_no_cross_tenant_rows(session, account_a, account_b, _pg_engine):
    """**A6** — the export contains no row belonging to another account.

    The marker is searched for in the **serialized bundle**, not row by row: a leak through an
    unexpected table is exactly the kind this would otherwise miss, and the bundle covers 49 of
    them. If the string appears anywhere, something crossed.
    """
    with _other_accounts_property(_pg_engine, account_b) as marker, \
            account_context(account_a):
        _seed_one_row_everywhere(session, account_a)
        bundle = build_export(session, account_a)

    serialized = json.dumps(bundle.tables, default=str)
    assert marker not in serialized, (
        f"account B's data appeared in account A's export ({marker})"
    )


def test_tenant_isolation(session, account_a, account_b, _pg_engine):
    """**A26** — a second account's data never appears in the first's export.

    A6's sibling, and deliberately not a duplicate: A6 asks whether any foreign row leaked, this
    asks whether the account's *own* rows are all there — a `build_export` that returned nothing
    at all would satisfy A6 perfectly.
    """
    with _other_accounts_property(_pg_engine, account_b), account_context(account_a):
        prop = _seed_one_row_everywhere(session, account_a)
        bundle = build_export(session, account_a)

    properties = bundle.tables["properties"]
    assert len(properties) == 1, f"expected exactly this account's property, got {properties}"
    assert properties[0]["name"] == "Export Manor"
    assert str(properties[0]["account_id"]) == str(account_a)
    assert properties[0]["id"] == str(prop.id)


def test_the_bundle_records_which_account_it_is_for(session, account_a):
    """A bundle with no owner is a support incident waiting to happen."""
    with account_context(account_a):
        bundle = build_export(session, account_a)

    assert bundle.account_id == str(account_a)
    assert bundle.generated_at is not None


def test_documents_are_references_never_bytes(session, account_a):
    """§5.4 — presigned references, never inlined content.

    An estate's media in a JSON blob is a file no browser opens, assembled by holding every
    byte in memory at once. The reference carries the key and a time-limited url; a backend
    with no urls (the filesystem one) legitimately yields `None`, and the key is still recorded.
    """
    from mihomes.models.document import DocumentType
    from mihomes.services import property as prop_svc
    from mihomes.services.document import create_document

    with account_context(account_a):
        prop = prop_svc.create_property(session, "Doc Manor")
        create_document(
            session,
            title="Deed",
            file_path="deeds/manor.pdf",
            document_type=DocumentType.CONTRACT,
            entity_type="property",
            entity_id=prop.id,
        )
        session.flush()
        bundle = build_export(session, account_a)

    assert bundle.documents, "a document was seeded and must appear"
    entry = bundle.documents[0]
    assert set(entry) == {"id", "title", "file_path", "url", "url_expires_in"}
    # No byte payload under any key.
    assert not any(
        isinstance(value, (bytes, bytearray)) for value in entry.values()
    ), entry


@pytest.mark.parametrize("forbidden", ["export_csv", "create_backup"])
def test_the_export_is_not_built_on_the_cross_tenant_helpers(forbidden):
    """**N4** — never `csv_io.export_csv`, never `backup.create_backup` (D14, F4/F5).

    A source-level assertion because the failure is invisible at runtime: both functions return
    data and neither raises. `export_csv` covers 5 of 28 model modules with no account filter, so
    an export built on it omits ~82% of the estate and looks fine; `create_backup` tars the whole
    database and media directory, which under multitenancy is a total breach wearing the name of
    a feature.
    """
    import inspect

    from mihomes.services.privacy import export as export_module

    source = inspect.getsource(export_module)
    # Strip the module docstring: it names both functions, at length, on purpose.
    body = source.split('"""', 2)[-1]
    assert forbidden not in body, (
        f"privacy/export.py must not call {forbidden} — see N4 and D14"
    )
