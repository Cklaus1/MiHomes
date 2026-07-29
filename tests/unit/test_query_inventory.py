"""Regression test for query_inventory rewrite (spec M44/Q4).

The old executor queried a phantom ``inventory_items`` table no migration
created, so every call raised "no such table". Inventory is now sourced from
the real ``Asset`` (durable, room-placed) and ``Consumable`` (stock) ORM
entities.
"""

import pytest

from mihomes.models.asset import Asset, AssetType
from mihomes.models.consumable import Consumable, ConsumableStatus
from mihomes.models.property import Property, PropertyType
from mihomes.models.space import Space
from mihomes.services.ai.tools import _query_inventory, execute_tool


@pytest.fixture
def db(session):
    return session


@pytest.fixture
def seeded(db):
    prop = Property(name="Beach House", slug="beach-house", property_type=PropertyType.PRIMARY)
    db.add(prop)
    db.flush()
    space = Space(name="Living Room", slug="living-room", property_id=prop.id, space_type="living")
    db.add(space)
    db.flush()
    db.add(Asset(name="Grand Piano", slug="grand-piano", asset_type=AssetType.VALUABLE,
                 property_id=prop.id, space_id=space.id, purchase_price=50000.0))
    db.add(Asset(name="Fridge", slug="fridge", asset_type=AssetType.APPLIANCE,
                 property_id=prop.id, space_id=space.id, purchase_price=2000.0))
    db.add(Consumable(name="Paper Towels", slug="paper-towels", property_id=prop.id,
                      category="Cleaning", unit="rolls", quantity_in_stock=12.0,
                      unit_price=1.5, status=ConsumableStatus.OK))
    db.flush()
    return prop


def test_query_inventory_returns_rows_no_missing_table(db, seeded):
    # Previously raised "no such table: inventory_items".
    out = execute_tool(db, "query_inventory", {})
    assert "no such table" not in out.lower()
    assert "Grand Piano" in out
    assert "Fridge" in out
    assert "Paper Towels" in out


def test_query_inventory_count_only(db, seeded):
    out = _query_inventory(db, {"count_only": True})
    assert "3 item(s)" in out
    # 50000 + 2000 assets + 12 * 1.5 consumables = 52018
    assert "52,018" in out


def test_high_value_only_returns_valuables(db, seeded):
    out = _query_inventory(db, {"high_value_only": True})
    assert "Grand Piano" in out
    assert "Fridge" not in out
    assert "Paper Towels" not in out  # consumables excluded when high_value_only


def test_property_filter_scopes_results(db, seeded):
    other = Property(name="Cabin", slug="cabin", property_type=PropertyType.VACATION)
    db.add(other)
    db.flush()
    db.add(Asset(name="Canoe", slug="canoe", asset_type=AssetType.EQUIPMENT,
                 property_id=other.id, purchase_price=800.0))
    db.flush()

    out = _query_inventory(db, {"property_slug": "beach-house"})
    assert "Grand Piano" in out
    assert "Canoe" not in out
