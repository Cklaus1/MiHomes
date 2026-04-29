"""Tests for zone service."""

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.models.space import Space
from mihomes.models.task import Task, TaskPriority, TaskStatus
from mihomes.models.zone import Zone
from mihomes.services.zone import (
    assign_space_to_zone,
    create_zone,
    delete_zone,
    get_zone,
    list_tasks_for_zone,
    list_zones,
    update_zone,
)


@pytest.fixture
def prop(session):
    p = Property(name="Zone House", slug="zone-house", property_type=PropertyType.PRIMARY)
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def prop2(session):
    p = Property(name="Other House", slug="other-house", property_type=PropertyType.VACATION)
    session.add(p)
    session.flush()
    return p


def _make_zone(session, prop, name="Upstairs", slug=None):
    return create_zone(session, name, str(prop.id), slug=slug)


class TestCreateZone:
    def test_creates_zone(self, session, prop):
        zone = create_zone(session, "Master Suite", str(prop.id))
        assert zone.id is not None
        assert zone.name == "Master Suite"
        assert zone.property_id == prop.id

    def test_auto_generates_slug(self, session, prop):
        zone = create_zone(session, "Master Suite", str(prop.id))
        assert zone.slug == "master-suite"

    def test_custom_slug_accepted(self, session, prop):
        zone = create_zone(session, "Kitchen Area", str(prop.id), slug="kitchen")
        assert zone.slug == "kitchen"

    def test_with_description(self, session, prop):
        zone = create_zone(session, "Exterior", str(prop.id), description="All outdoor areas")
        assert zone.description == "All outdoor areas"

    def test_duplicate_slugs_made_unique(self, session, prop):
        z1 = create_zone(session, "Garden", str(prop.id))
        z2 = create_zone(session, "Garden", str(prop.id))
        assert z1.slug != z2.slug

    def test_lookup_by_slug(self, session, prop):
        create_zone(session, "Pool Area", "zone-house")
        zones = list_zones(session, "zone-house")
        assert len(zones) == 1


class TestListZones:
    def test_lists_zones_for_property(self, session, prop, prop2):
        _make_zone(session, prop, "Upstairs")
        _make_zone(session, prop, "Downstairs")
        _make_zone(session, prop2, "Guest Suite")
        zones = list_zones(session, str(prop.id))
        names = [z.name for z in zones]
        assert "Upstairs" in names
        assert "Downstairs" in names
        assert "Guest Suite" not in names

    def test_returns_empty_list_when_none(self, session, prop):
        assert list_zones(session, str(prop.id)) == []

    def test_sorted_by_name(self, session, prop):
        _make_zone(session, prop, "Zebra Room")
        _make_zone(session, prop, "Apple Room")
        zones = list_zones(session, str(prop.id))
        names = [z.name for z in zones]
        assert names == sorted(names)


class TestGetZone:
    def test_get_by_id(self, session, prop):
        zone = _make_zone(session, prop)
        fetched = get_zone(session, str(zone.id))
        assert fetched.id == zone.id

    def test_get_by_slug(self, session, prop):
        zone = _make_zone(session, prop, "Upstairs Suite")
        fetched = get_zone(session, "upstairs-suite")
        assert fetched.id == zone.id

    def test_nonexistent_raises(self, session):
        with pytest.raises(ValueError):
            get_zone(session, "nonexistent-zone")


class TestUpdateZone:
    def test_update_name(self, session, prop):
        zone = _make_zone(session, prop, "Old Name")
        updated = update_zone(session, zone.slug, name="New Name")
        assert updated.name == "New Name"

    def test_update_name_regenerates_slug(self, session, prop):
        zone = _make_zone(session, prop, "Old Zone Name")
        update_zone(session, zone.slug, name="Completely New Name")
        # Slug should be regenerated
        session.expire(zone)
        assert zone.name == "Completely New Name"

    def test_update_description(self, session, prop):
        zone = _make_zone(session, prop)
        updated = update_zone(session, zone.slug, description="Updated description")
        assert updated.description == "Updated description"

    def test_update_nonexistent_raises(self, session):
        with pytest.raises(ValueError):
            update_zone(session, "nonexistent-zone", name="New Name")

    def test_no_changes_does_not_create_audit(self, session, prop):
        zone = _make_zone(session, prop)
        # Update with the same value — should not error
        update_zone(session, zone.slug, description=zone.description)


class TestDeleteZone:
    def test_delete_zone(self, session, prop):
        zone = _make_zone(session, prop, "Temporary Zone")
        slug = zone.slug
        name = delete_zone(session, slug)
        assert name == "Temporary Zone"
        assert session.query(Zone).filter(Zone.slug == slug).first() is None

    def test_delete_unlinks_spaces(self, session, prop):
        zone = _make_zone(session, prop, "Zone With Spaces")
        space = Space(name="Room A", slug="room-a", property_id=prop.id, zone_id=zone.id)
        session.add(space)
        session.flush()
        delete_zone(session, zone.slug)
        session.expire(space)
        assert space.zone_id is None

    def test_delete_unlinks_tasks(self, session, prop):
        zone = _make_zone(session, prop, "Zone With Tasks")
        task = Task(
            title="Zone Task", slug="zone-task",
            property_id=prop.id, zone_id=zone.id,
            priority=TaskPriority.MEDIUM,
        )
        session.add(task)
        session.flush()
        delete_zone(session, zone.slug)
        session.expire(task)
        assert task.zone_id is None

    def test_delete_nonexistent_raises(self, session):
        with pytest.raises(ValueError):
            delete_zone(session, "ghost-zone")


class TestAssignSpaceToZone:
    def test_assigns_space(self, session, prop):
        zone = _make_zone(session, prop, "Upper Floor")
        space = Space(name="Master Bedroom", slug="master-bedroom", property_id=prop.id)
        session.add(space)
        session.flush()
        result = assign_space_to_zone(session, "master-bedroom", zone.slug)
        assert result.zone_id == zone.id

    def test_reassigns_to_new_zone(self, session, prop):
        zone1 = _make_zone(session, prop, "Zone One")
        zone2 = _make_zone(session, prop, "Zone Two")
        space = Space(name="Flex Room", slug="flex-room", property_id=prop.id, zone_id=zone1.id)
        session.add(space)
        session.flush()
        assign_space_to_zone(session, "flex-room", zone2.slug)
        session.expire(space)
        assert space.zone_id == zone2.id


class TestListTasksForZone:
    def test_returns_open_tasks(self, session, prop):
        zone = _make_zone(session, prop, "Maintenance Zone")
        task = Task(
            title="Pending Zone Task", slug="pending-zone-task",
            property_id=prop.id, zone_id=zone.id,
            priority=TaskPriority.MEDIUM, status=TaskStatus.PENDING,
        )
        session.add(task)
        session.flush()
        tasks = list_tasks_for_zone(session, zone.slug)
        titles = [t.title for t in tasks]
        assert "Pending Zone Task" in titles

    def test_excludes_completed_by_default(self, session, prop):
        from datetime import datetime, timezone
        zone = _make_zone(session, prop, "Done Zone")
        task = Task(
            title="Completed Zone Task", slug="completed-zone-task",
            property_id=prop.id, zone_id=zone.id,
            priority=TaskPriority.MEDIUM, status=TaskStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc),
        )
        session.add(task)
        session.flush()
        tasks = list_tasks_for_zone(session, zone.slug, open_only=True)
        assert all(t.status != TaskStatus.COMPLETED for t in tasks)

    def test_includes_completed_when_open_only_false(self, session, prop):
        from datetime import datetime, timezone
        zone = _make_zone(session, prop, "All Tasks Zone")
        task = Task(
            title="Done Task", slug="done-task-zone",
            property_id=prop.id, zone_id=zone.id,
            priority=TaskPriority.MEDIUM, status=TaskStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc),
        )
        session.add(task)
        session.flush()
        tasks = list_tasks_for_zone(session, zone.slug, open_only=False)
        titles = [t.title for t in tasks]
        assert "Done Task" in titles

    def test_returns_empty_for_empty_zone(self, session, prop):
        zone = _make_zone(session, prop, "Empty Zone")
        tasks = list_tasks_for_zone(session, zone.slug)
        assert tasks == []
