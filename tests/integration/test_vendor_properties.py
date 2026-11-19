"""M14 (R4.7): vendor↔property normalization — the association table.

The migration-level half of this file is gone (SPEC-002 G6.3). It replayed the legacy
SQLite chain to assert that the M14 migration unnested `vendors.property_ids` (a JSON blob)
into `vendor_properties`, dropping a dangling id along the way. That chain now lives in
`alembic/legacy_sqlite/` and never runs, and the test was written against **integer**
primary keys (`'[1, 2, 99999]'`) which G6.1 replaced with UUIDv7 — so it could not be
repaired, only deleted. `0001_pg_baseline` creates `vendor_properties` directly.

The service-level tests below are the part that still describes live behaviour.
"""

from mihomes.models.property import Property, PropertyType
from mihomes.services.vendor import create_vendor, get_vendor, update_vendor

# --- service-level: create/update round-trip through the relationship --------

def _prop(session, slug):
    p = Property(name=slug.title(), slug=slug, property_type=PropertyType.PRIMARY)
    session.add(p)
    session.flush()
    return p


def test_create_vendor_links_properties(session):
    a = _prop(session, "alpha")
    b = _prop(session, "bravo")
    v = create_vendor(session, "Acme", property_ids=[a.id, b.id])
    session.flush()
    assert {p.id for p in v.properties} == {a.id, b.id}
    assert sorted(v.property_ids) == sorted([a.id, b.id])  # read-only view works
    # reverse navigation via backref
    assert v in a.vendors


def test_update_vendor_replaces_links(session):
    a = _prop(session, "alpha")
    b = _prop(session, "bravo")
    v = create_vendor(session, "Acme", property_ids=[a.id])
    session.flush()
    update_vendor(session, v.slug, property_ids=[b.id])
    session.flush()
    refreshed = get_vendor(session, v.slug)
    assert [p.id for p in refreshed.properties] == [b.id]


def test_update_vendor_clear_links(session):
    a = _prop(session, "alpha")
    v = create_vendor(session, "Acme", property_ids=[a.id])
    session.flush()
    update_vendor(session, v.slug, property_ids=[])
    session.flush()
    assert get_vendor(session, v.slug).properties == []
