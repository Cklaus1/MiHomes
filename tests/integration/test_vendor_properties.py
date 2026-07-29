"""M14 (R4.7): vendor↔property normalization — association table + migration."""

import sqlite3

import pytest
from alembic import command

from mihomes.models.property import Property, PropertyType
from mihomes.models.vendor import Vendor
from mihomes.services.vendor import create_vendor, get_vendor, update_vendor
from tests.integration.test_migration_reconciliation import _cfg, PARENT


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


# --- migration-level: JSON blob is unnested into the association table -------

@pytest.fixture
def db_at_parent(tmp_path):
    """A file DB migrated to the revision *before* the M14 migration."""
    db_path = tmp_path / "mihomes.db"
    url = f"sqlite:///{db_path}"
    cfg = _cfg(url)
    command.upgrade(cfg, PARENT)
    return {"url": url, "path": str(db_path), "cfg": cfg}


def test_migration_unnests_json_property_ids(db_at_parent):
    path = db_at_parent["path"]
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO properties (name, slug, property_type, status, currency, "
                "occupied, created_at, updated_at) VALUES "
                "('P1','p1','PRIMARY','OPEN','USD',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    con.execute("INSERT INTO properties (name, slug, property_type, status, currency, "
                "occupied, created_at, updated_at) VALUES "
                "('P2','p2','PRIMARY','OPEN','USD',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    # a vendor tagged to both properties + one dangling id (99999) that must drop
    con.execute("INSERT INTO vendors (company_name, slug, active, property_ids, "
                "created_at, updated_at) VALUES "
                "('Acme','acme',1,'[1, 2, 99999]',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    con.commit()
    con.close()

    # migrate through the M14 migration (and the reconciliation before it)
    command.upgrade(db_at_parent["cfg"], "head")

    con = sqlite3.connect(path)
    rows = con.execute(
        "SELECT property_id FROM vendor_properties WHERE vendor_id="
        "(SELECT id FROM vendors WHERE slug='acme') ORDER BY property_id"
    ).fetchall()
    # dangling 99999 dropped by the INNER JOIN; 1 and 2 preserved
    assert [r[0] for r in rows] == [1, 2]
    # legacy column is gone
    cols = [r[1] for r in con.execute("PRAGMA table_info(vendors)").fetchall()]
    con.close()
    assert "property_ids" not in cols
