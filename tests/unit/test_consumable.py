"""Tests for consumable inventory service."""

from mihomes.models.consumable import ConsumableStatus
from mihomes.models.property import Property, PropertyType
from mihomes.services.consumable import (
    _compute_status,
    create_consumable,
    get_or_create_consumable,
    get_reorder_list,
    list_consumables,
    mark_ordered,
    mark_restocked,
    update_stock,
)


def _make_property(session, name="Belle Estate", slug="belle-estate"):
    prop = Property(name=name, slug=slug, property_type=PropertyType.PRIMARY)
    session.add(prop)
    session.flush()
    return prop


class TestComputeStatus:
    def test_none_quantity_returns_ok(self):
        assert _compute_status(None, None) == ConsumableStatus.OK

    def test_zero_quantity_returns_out(self):
        assert _compute_status(0, 6) == ConsumableStatus.OUT

    def test_negative_quantity_returns_out(self):
        assert _compute_status(-1, 6) == ConsumableStatus.OUT

    def test_par_level_one_has_no_separate_low_zone(self):
        # par_level == 1: nothing in between "fine" and "out" — OUT already
        # covers running out, so there's no separate LOW warning zone.
        assert _compute_status(1, 1) == ConsumableStatus.OK
        assert _compute_status(0, 1) == ConsumableStatus.OUT

    def test_par_level_above_one_flags_low_at_fixed_threshold(self):
        # Default LOW threshold is a fixed "fewer than 2 units left,"
        # not proportional to how high par_level is — flagging LOW at
        # 19/20 in stock (the old qty <= par_level behavior) is a false alarm.
        assert _compute_status(1, 6) == ConsumableStatus.LOW
        assert _compute_status(2, 6) == ConsumableStatus.OK
        assert _compute_status(19, 20) == ConsumableStatus.OK

    def test_above_par_level_returns_ok(self):
        assert _compute_status(10, 6) == ConsumableStatus.OK

    def test_no_par_level_with_stock_returns_ok(self):
        assert _compute_status(5, None) == ConsumableStatus.OK

    def test_custom_threshold_overrides_default_formula(self):
        assert _compute_status(4, 20, low_stock_threshold=5) == ConsumableStatus.LOW
        assert _compute_status(5, 20, low_stock_threshold=5) == ConsumableStatus.OK


class TestCreateConsumable:
    def test_create_basic(self, session):
        prop = _make_property(session)
        item = create_consumable(session, "Pool Chemicals", str(prop.id))
        assert item.id is not None
        assert item.name == "Pool Chemicals"
        assert item.property_id == prop.id
        assert item.status == ConsumableStatus.OK

    def test_create_with_all_fields(self, session):
        prop = _make_property(session)
        item = create_consumable(
            session, "Trash Bags", str(prop.id),
            unit="box", category="cleaning",
            par_level=4, quantity_in_stock=2, notes="under sink"
        )
        assert item.unit == "box"
        assert item.category == "cleaning"
        assert item.par_level == 4
        assert item.quantity_in_stock == 2
        assert item.status == ConsumableStatus.OK
        assert item.notes == "under sink"

    def test_status_computed_on_create(self, session):
        prop = _make_property(session)
        item = create_consumable(session, "Paper Towels", str(prop.id), quantity_in_stock=0, par_level=3)
        assert item.status == ConsumableStatus.OUT

    def test_slug_generated(self, session):
        prop = _make_property(session)
        item = create_consumable(session, "Pool Chemicals", str(prop.id))
        assert item.slug == "pool-chemicals"

    def test_duplicate_slugs_unique(self, session):
        prop = _make_property(session)
        i1 = create_consumable(session, "Pool Chemicals", str(prop.id))
        i2 = create_consumable(session, "Pool Chemicals", str(prop.id))
        assert i1.slug != i2.slug


class TestGetOrCreateConsumable:
    def test_creates_when_not_found(self, session):
        prop = _make_property(session)
        item = get_or_create_consumable(session, "Bleach", str(prop.id))
        assert item.id is not None
        assert item.name == "Bleach"

    def test_returns_existing_case_insensitive(self, session):
        prop = _make_property(session)
        item1 = get_or_create_consumable(session, "Bleach", str(prop.id))
        item2 = get_or_create_consumable(session, "bleach", str(prop.id))
        assert item1.id == item2.id

    def test_sets_unit_on_create(self, session):
        prop = _make_property(session)
        item = get_or_create_consumable(session, "Bleach", str(prop.id), unit="gallon")
        assert item.unit == "gallon"


class TestUpdateStock:
    def test_update_quantity(self, session):
        prop = _make_property(session)
        create_consumable(session, "Pool Salt", str(prop.id), par_level=5)
        updated = update_stock(session, "pool-salt", str(prop.id), quantity_in_stock=8)
        assert updated.quantity_in_stock == 8
        assert updated.status == ConsumableStatus.OK

    def test_update_quantity_to_low(self, session):
        prop = _make_property(session)
        create_consumable(session, "Pool Salt", str(prop.id), par_level=5, quantity_in_stock=10)
        updated = update_stock(session, "Pool Salt", str(prop.id), quantity_in_stock=1)
        assert updated.status == ConsumableStatus.LOW

    def test_update_order_quantity(self, session):
        prop = _make_property(session)
        create_consumable(session, "Pool Salt", str(prop.id), quantity_in_stock=10, par_level=3)
        updated = update_stock(session, "Pool Salt", str(prop.id), quantity_to_order=5)
        assert updated.quantity_to_order == 5

    def test_order_quantity_sets_low_if_ok(self, session):
        prop = _make_property(session)
        create_consumable(session, "Pool Salt", str(prop.id), quantity_in_stock=10, par_level=3)
        updated = update_stock(session, "Pool Salt", str(prop.id), quantity_to_order=2)
        assert updated.status == ConsumableStatus.LOW

    def test_creates_if_not_found(self, session):
        prop = _make_property(session)
        item = update_stock(session, "New Item", str(prop.id), quantity_in_stock=5)
        assert item.id is not None

    def test_sets_last_updated_by(self, session):
        prop = _make_property(session)
        create_consumable(session, "Soap", str(prop.id))
        updated = update_stock(session, "soap", str(prop.id), quantity_in_stock=3, updated_by="maria")
        assert updated.last_updated_by == "maria"


class TestListConsumables:
    def test_list_all(self, session):
        prop = _make_property(session)
        create_consumable(session, "Item A", str(prop.id))
        create_consumable(session, "Item B", str(prop.id))
        items = list_consumables(session)
        assert len(items) == 2

    def test_filter_by_property(self, session):
        prop1 = _make_property(session, "House 1", "house-1")
        prop2 = _make_property(session, "House 2", "house-2")
        create_consumable(session, "Item A", str(prop1.id))
        create_consumable(session, "Item B", str(prop2.id))
        items = list_consumables(session, property_id_or_slug="house-1")
        assert len(items) == 1
        assert items[0].name == "Item A"

    def test_filter_needs_reorder(self, session):
        prop = _make_property(session)
        create_consumable(session, "Low Item", str(prop.id), quantity_in_stock=1, par_level=5)
        create_consumable(session, "OK Item", str(prop.id), quantity_in_stock=10, par_level=5)
        items = list_consumables(session, needs_reorder=True)
        assert len(items) == 1
        assert items[0].name == "Low Item"

    def test_empty_returns_empty_list(self, session):
        assert list_consumables(session) == []


class TestMarkOrdered:
    def test_mark_ordered(self, session):
        prop = _make_property(session)
        item = create_consumable(session, "Filters", str(prop.id), quantity_in_stock=0, par_level=4)
        result = mark_ordered(session, item.slug)
        assert result.status == ConsumableStatus.ORDERED
        assert result.quantity_to_order is None


class TestMarkRestocked:
    def test_mark_restocked_with_quantity(self, session):
        prop = _make_property(session)
        item = create_consumable(session, "Filters", str(prop.id), par_level=4)
        result = mark_restocked(session, item.slug, quantity=10)
        assert result.quantity_in_stock == 10
        assert result.status == ConsumableStatus.OK
        assert result.quantity_to_order is None

    def test_mark_restocked_without_quantity(self, session):
        prop = _make_property(session)
        item = create_consumable(session, "Filters", str(prop.id), quantity_in_stock=0, par_level=4)
        result = mark_restocked(session, item.slug)
        assert result.status == ConsumableStatus.OUT  # still 0, no quantity passed


class TestGetReorderList:
    def test_returns_low_and_out(self, session):
        prop = _make_property(session)
        create_consumable(session, "Low", str(prop.id), quantity_in_stock=1, par_level=5)
        create_consumable(session, "Out", str(prop.id), quantity_in_stock=0, par_level=5)
        create_consumable(session, "OK", str(prop.id), quantity_in_stock=10, par_level=5)
        items = get_reorder_list(session)
        names = {i.name for i in items}
        assert "Low" in names
        assert "Out" in names
        assert "OK" not in names

    def test_filter_by_property(self, session):
        prop1 = _make_property(session, "House 1", "house-1")
        prop2 = _make_property(session, "House 2", "house-2")
        create_consumable(session, "Low A", str(prop1.id), quantity_in_stock=1, par_level=5)
        create_consumable(session, "Low B", str(prop2.id), quantity_in_stock=1, par_level=5)
        items = get_reorder_list(session, property_id_or_slug="house-1")
        assert len(items) == 1
        assert items[0].name == "Low A"
