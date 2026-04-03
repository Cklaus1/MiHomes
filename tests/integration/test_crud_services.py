"""Integration tests for CRUD services with low coverage.

Exercises update, delete, filter, and lifecycle paths missed by existing tests.
"""

from datetime import date, timedelta

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.models.staff import Staff, StaffRole
from mihomes.models.vendor import Vendor
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.event import Event, EventStatus, Guest
from mihomes.models.asset import Asset, AssetType
from mihomes.models.tag import Tag, TagAssignment
from mihomes.models.alert import Alert, AlertStatus, AlertSeverity


@pytest.fixture
def prop(session):
    p = Property(name="CRUD House", slug="crud-house",
                 property_type=PropertyType.PRIMARY, currency="USD")
    session.add(p)
    session.flush()
    return p


# ---------------------------------------------------------------------------
# Staff service
# ---------------------------------------------------------------------------

class TestStaffService:
    def test_update_name_regenerates_slug(self, session, prop):
        from mihomes.services.staff import create_staff, update_staff
        s = create_staff(session, "Old Name", role=StaffRole.HOUSEKEEPER,
                         property_id_or_slug=str(prop.id))
        update_staff(session, s.slug, name="New Name")
        session.expire(s)
        assert s.name == "New Name"
        assert s.slug == "new-name"

    def test_update_phone(self, session, prop):
        from mihomes.services.staff import create_staff, update_staff
        s = create_staff(session, "Jane Smith", role=StaffRole.HOUSEKEEPER,
                         property_id_or_slug=str(prop.id))
        update_staff(session, s.slug, phone="555-9999")
        session.expire(s)
        assert s.phone == "555-9999"

    def test_delete_returns_name(self, session, prop):
        from mihomes.services.staff import create_staff, delete_staff
        s = create_staff(session, "To Delete", role=StaffRole.GROUNDSKEEPER,
                         property_id_or_slug=str(prop.id))
        slug = s.slug
        name = delete_staff(session, slug)
        assert name == "To Delete"
        assert session.query(Staff).filter(Staff.slug == slug).first() is None

    def test_assign_and_remove_property(self, session, prop):
        from mihomes.services.staff import create_staff, assign_to_property, remove_from_property
        p2 = Property(name="Second Property", slug="second-property",
                      property_type=PropertyType.VACATION, currency="USD")
        session.add(p2)
        session.flush()
        s = create_staff(session, "Multi Staff", role=StaffRole.HOUSEKEEPER,
                         property_id_or_slug=str(prop.id))
        assign_to_property(session, s.slug, p2.slug)
        session.expire(s)
        prop_names = [p.name for p in s.properties]
        assert "Second Property" in prop_names

        remove_from_property(session, s.slug, p2.slug)
        session.expire(s)
        prop_names = [p.name for p in s.properties]
        assert "Second Property" not in prop_names

    def test_assign_already_assigned_is_noop(self, session, prop):
        from mihomes.services.staff import create_staff, assign_to_property
        s = create_staff(session, "Already Assigned", role=StaffRole.HOUSEKEEPER,
                         property_id_or_slug=str(prop.id))
        assign_to_property(session, s.slug, prop.slug)
        session.expire(s)
        assert len(s.properties) == 1

    def test_list_by_role(self, session, prop):
        from mihomes.services.staff import create_staff, list_staff
        create_staff(session, "Housekeeper A", role=StaffRole.HOUSEKEEPER,
                     property_id_or_slug=str(prop.id))
        create_staff(session, "Groundskeeper B", role=StaffRole.GROUNDSKEEPER,
                     property_id_or_slug=str(prop.id))
        housekeepers = list_staff(session, role=StaffRole.HOUSEKEEPER)
        assert all(s.role == StaffRole.HOUSEKEEPER for s in housekeepers)

    def test_list_by_property(self, session, prop):
        from mihomes.services.staff import create_staff, list_by_property
        create_staff(session, "Staff For Prop", role=StaffRole.HOUSEKEEPER,
                     property_id_or_slug=str(prop.id))
        result = list_by_property(session, prop.slug)
        assert any(s.name == "Staff For Prop" for s in result)


# ---------------------------------------------------------------------------
# Vendor service
# ---------------------------------------------------------------------------

class TestVendorService:
    def test_update_company_name_regenerates_slug(self, session):
        from mihomes.services.vendor import create_vendor, update_vendor
        v = create_vendor(session, "Old Vendor Co")
        update_vendor(session, v.slug, company_name="New Vendor Co")
        session.expire(v)
        assert v.company_name == "New Vendor Co"
        assert v.slug == "new-vendor-co"

    def test_update_contact(self, session):
        from mihomes.services.vendor import create_vendor, update_vendor
        v = create_vendor(session, "Update Contact Co")
        update_vendor(session, v.slug, contact_name="Jane Doe", phone="555-1111")
        session.expire(v)
        assert v.contact_name == "Jane Doe"

    def test_delete_returns_name(self, session):
        from mihomes.services.vendor import create_vendor, delete_vendor
        v = create_vendor(session, "Delete Vendor Co")
        slug = v.slug
        name = delete_vendor(session, slug)
        assert name == "Delete Vendor Co"
        assert session.query(Vendor).filter(Vendor.slug == slug).first() is None

    def test_filter_by_category(self, session):
        from mihomes.services.vendor import create_vendor, list_vendors
        create_vendor(session, "Plumber Pro", service_categories=["plumbing", "drainage"])
        create_vendor(session, "Sparky Electric", service_categories=["electrical"])
        plumbers = list_vendors(session, category="plumbing")
        names = [v.company_name for v in plumbers]
        assert "Plumber Pro" in names
        assert "Sparky Electric" not in names

    def test_list_active_only(self, session):
        from mihomes.services.vendor import create_vendor, update_vendor, list_vendors
        v = create_vendor(session, "Inactive Vendor")
        update_vendor(session, v.slug, active=False)
        active_only = list_vendors(session)
        inactive_included = list_vendors(session, active_only=False)
        assert not any(x.slug == v.slug for x in active_only)
        assert any(x.slug == v.slug for x in inactive_included)


# ---------------------------------------------------------------------------
# Space service
# ---------------------------------------------------------------------------

class TestSpaceService:
    def test_list_spaces(self, session, prop):
        from mihomes.services.space import create_space, list_spaces
        create_space(session, "Kitchen", str(prop.id), space_type="kitchen")
        create_space(session, "Living Room", str(prop.id), space_type="living_room")
        spaces = list_spaces(session, str(prop.id))
        names = [s.name for s in spaces]
        assert "Kitchen" in names
        assert "Living Room" in names

    def test_get_space_by_slug(self, session, prop):
        from mihomes.services.space import create_space, get_space
        s = create_space(session, "Master Bedroom", str(prop.id))
        fetched = get_space(session, s.slug)
        assert fetched.id == s.id

    def test_delete_space(self, session, prop):
        from mihomes.services.space import create_space, delete_space
        from mihomes.models.space import Space
        s = create_space(session, "Utility Room", str(prop.id))
        slug = s.slug
        name = delete_space(session, slug)
        assert name == "Utility Room"
        assert session.query(Space).filter(Space.slug == slug).first() is None


# ---------------------------------------------------------------------------
# Issue service
# ---------------------------------------------------------------------------

class TestIssueService:
    def test_update_issue(self, session, prop):
        from mihomes.services.issue import create_issue, update_issue
        issue = create_issue(session, "Broken Window", prop.slug)
        update_issue(session, issue.slug, title="Cracked Window")
        session.expire(issue)
        assert issue.title == "Cracked Window"
        assert issue.slug == "cracked-window"

    def test_update_severity(self, session, prop):
        from mihomes.services.issue import create_issue, update_issue
        issue = create_issue(session, "Small Leak", prop.slug, severity=IssueSeverity.LOW)
        update_issue(session, issue.slug, severity=IssueSeverity.HIGH)
        session.expire(issue)
        assert issue.severity == IssueSeverity.HIGH

    def test_resolve_issue(self, session, prop):
        from mihomes.services.issue import create_issue, resolve_issue
        issue = create_issue(session, "Stain on Ceiling", prop.slug)
        resolved = resolve_issue(session, issue.slug, notes="Repainted")
        assert resolved.status == IssueStatus.RESOLVED
        assert resolved.resolution_notes == "Repainted"
        assert resolved.resolved_at is not None

    def test_resolve_clears_alerts(self, session, prop):
        from mihomes.services.issue import create_issue, resolve_issue
        issue = create_issue(session, "Alert Issue", prop.slug)
        alert = Alert(
            alert_type="issue_open", message="Issue open",
            severity=AlertSeverity.HIGH,
            source_entity_type="issue", source_entity_id=issue.id,
            status=AlertStatus.GENERATED,
        )
        session.add(alert)
        session.flush()
        resolve_issue(session, issue.slug)
        session.expire(alert)
        assert alert.status == AlertStatus.RESOLVED

    def test_delete_issue(self, session, prop):
        from mihomes.services.issue import create_issue, delete_issue
        issue = create_issue(session, "Delete This Issue", prop.slug)
        slug = issue.slug
        name = delete_issue(session, slug)
        assert name == "Delete This Issue"
        assert session.query(Issue).filter(Issue.slug == slug).first() is None

    def test_filter_open_only(self, session, prop):
        from mihomes.services.issue import create_issue, resolve_issue, list_issues
        create_issue(session, "Open Issue", prop.slug)
        closed = create_issue(session, "Closed Issue", prop.slug)
        resolve_issue(session, closed.slug)
        open_issues = list_issues(session, open_only=True)
        titles = [i.title for i in open_issues]
        assert "Open Issue" in titles
        assert "Closed Issue" not in titles

    def test_filter_resolved_only(self, session, prop):
        from mihomes.services.issue import create_issue, resolve_issue, list_issues
        create_issue(session, "Still Open", prop.slug)
        closed = create_issue(session, "Now Resolved", prop.slug)
        resolve_issue(session, closed.slug)
        resolved = list_issues(session, resolved_only=True)
        titles = [i.title for i in resolved]
        assert "Now Resolved" in titles
        assert "Still Open" not in titles


# ---------------------------------------------------------------------------
# Event and Guest service
# ---------------------------------------------------------------------------

class TestEventService:
    def test_update_event(self, session, prop):
        from mihomes.services.event import create_event, update_event
        evt = create_event(session, "Summer Party", prop.slug,
                           date.today() + timedelta(days=30))
        update_event(session, evt.slug, title="Annual Summer Party")
        session.expire(evt)
        assert evt.title == "Annual Summer Party"
        assert evt.slug == "annual-summer-party"

    def test_delete_event(self, session, prop):
        from mihomes.services.event import create_event, delete_event
        evt = create_event(session, "Delete Event", prop.slug,
                           date.today() + timedelta(days=10))
        slug = evt.slug
        name = delete_event(session, slug)
        assert name == "Delete Event"
        assert session.query(Event).filter(Event.slug == slug).first() is None

    def test_filter_by_status(self, session, prop):
        from mihomes.services.event import create_event, update_event, list_events
        e1 = create_event(session, "Confirmed Event", prop.slug,
                          date.today() + timedelta(days=10))
        update_event(session, e1.slug, status=EventStatus.CONFIRMED)
        create_event(session, "Planning Event", prop.slug,
                     date.today() + timedelta(days=20))
        confirmed = list_events(session, status=EventStatus.CONFIRMED)
        assert all(e.status == EventStatus.CONFIRMED for e in confirmed)

    def test_filter_by_property(self, session, prop):
        from mihomes.services.event import create_event, list_events
        p2 = Property(name="Other Place", slug="other-place",
                      property_type=PropertyType.VACATION, currency="USD")
        session.add(p2)
        session.flush()
        create_event(session, "My Event", prop.slug, date.today() + timedelta(days=5))
        create_event(session, "Other Event", p2.slug, date.today() + timedelta(days=5))
        results = list_events(session, property_id_or_slug=prop.slug)
        titles = [e.title for e in results]
        assert "My Event" in titles
        assert "Other Event" not in titles

    def test_create_guest(self, session):
        from mihomes.services.event import create_guest, get_guest
        g = create_guest(session, "Alice Smith", email="alice@example.com",
                         dietary_preferences="Vegetarian")
        assert g.id is not None
        assert g.email == "alice@example.com"
        fetched = get_guest(session, g.slug)
        assert fetched.id == g.id

    def test_list_guests(self, session):
        from mihomes.services.event import create_guest, list_guests
        create_guest(session, "Bob Jones")
        create_guest(session, "Carol White")
        guests = list_guests(session)
        names = [g.name for g in guests]
        assert "Bob Jones" in names
        assert "Carol White" in names

    def test_delete_guest(self, session):
        from mihomes.services.event import create_guest, delete_guest
        g = create_guest(session, "Delete Guest")
        slug = g.slug
        name = delete_guest(session, slug)
        assert name == "Delete Guest"
        assert session.query(Guest).filter(Guest.slug == slug).first() is None


# ---------------------------------------------------------------------------
# Asset service
# ---------------------------------------------------------------------------

class TestAssetService:
    def test_update_asset(self, session, prop):
        from mihomes.services.asset import create_asset, update_asset
        a = create_asset(session, "Washing Machine", AssetType.APPLIANCE, prop.slug)
        update_asset(session, a.slug, purchase_price=800.0)
        session.expire(a)
        assert a.purchase_price == 800.0

    def test_delete_asset(self, session, prop):
        from mihomes.services.asset import create_asset, delete_asset
        a = create_asset(session, "Old Fridge", AssetType.APPLIANCE, prop.slug)
        slug = a.slug
        name = delete_asset(session, slug)
        assert name == "Old Fridge"
        assert session.query(Asset).filter(Asset.slug == slug).first() is None

    def test_list_by_type(self, session, prop):
        from mihomes.services.asset import create_asset, list_assets
        create_asset(session, "Car", AssetType.VEHICLE, prop.slug)
        create_asset(session, "Dishwasher", AssetType.APPLIANCE, prop.slug)
        vehicles = list_assets(session, asset_type=AssetType.VEHICLE)
        assert all(a.asset_type == AssetType.VEHICLE for a in vehicles)

    def test_list_by_property(self, session, prop):
        from mihomes.services.asset import create_asset, list_assets
        create_asset(session, "Pool Pump", AssetType.EQUIPMENT, prop.slug)
        results = list_assets(session, property_id_or_slug=prop.slug)
        assert any(a.name == "Pool Pump" for a in results)


# ---------------------------------------------------------------------------
# Tag service
# ---------------------------------------------------------------------------

class TestTagService:
    def test_create_tag_idempotent(self, session):
        from mihomes.services.tag import create_tag
        t1 = create_tag(session, "urgent")
        t2 = create_tag(session, "urgent")
        assert t1.id == t2.id

    def test_create_tag_lowercased(self, session):
        from mihomes.services.tag import create_tag
        t = create_tag(session, "Safety")
        assert t.name == "safety"

    def test_apply_and_get_tagged_entities(self, session, prop):
        from mihomes.services.tag import apply_tag, get_tagged_entities
        from mihomes.services.issue import create_issue
        issue = create_issue(session, "Tagged Issue", prop.slug)
        apply_tag(session, "needs-attention", [f"issue:{issue.slug}"])
        entities = get_tagged_entities(session, "needs-attention")
        ids = [e["id"] for e in entities]
        assert issue.id in ids

    def test_remove_tag(self, session, prop):
        from mihomes.services.tag import apply_tag, remove_tag, get_tagged_entities
        from mihomes.services.issue import create_issue
        issue = create_issue(session, "Untagged Issue", prop.slug)
        apply_tag(session, "temp-tag", [f"issue:{issue.slug}"])
        removed = remove_tag(session, "temp-tag", f"issue:{issue.slug}")
        assert removed is True
        entities = get_tagged_entities(session, "temp-tag")
        assert issue.id not in [e["id"] for e in entities]

    def test_remove_nonexistent_tag_returns_false(self, session, prop):
        from mihomes.services.tag import remove_tag
        from mihomes.services.issue import create_issue
        issue = create_issue(session, "No Tag Issue", prop.slug)
        result = remove_tag(session, "ghost-tag", f"issue:{issue.slug}")
        assert result is False

    def test_apply_tag_is_idempotent(self, session, prop):
        from mihomes.services.tag import apply_tag
        from mihomes.services.issue import create_issue
        issue = create_issue(session, "Idempotent Tag Issue", prop.slug)
        apply_tag(session, "idempotent", [f"issue:{issue.slug}"])
        apply_tag(session, "idempotent", [f"issue:{issue.slug}"])
        count = session.query(TagAssignment).join(Tag).filter(
            Tag.name == "idempotent"
        ).count()
        assert count == 1


# ---------------------------------------------------------------------------
# Alerts service
# ---------------------------------------------------------------------------

class TestAlertsService:
    def test_generate_returns_count(self, session, prop):
        from mihomes.services.alerts import generate_alerts
        from mihomes.models.task import Task, TaskPriority, TaskStatus
        t = Task(title="Overdue Alert Task", slug="overdue-alert-task",
                 property_id=prop.id, priority=TaskPriority.HIGH,
                 status=TaskStatus.PENDING,
                 due_date=date.today() - timedelta(days=3))
        session.add(t)
        session.flush()
        count = generate_alerts(session)
        assert isinstance(count, int)
        assert count >= 0

    def test_list_active_alerts(self, session):
        from mihomes.services.alerts import list_alerts
        alert = Alert(
            alert_type="test", message="Test active alert",
            severity=AlertSeverity.HIGH, status=AlertStatus.GENERATED,
        )
        session.add(alert)
        session.flush()
        actives = list_alerts(session)
        assert any(a.message == "Test active alert" for a in actives)

    def test_acknowledge_alert(self, session):
        from mihomes.services.alerts import acknowledge_alert
        alert = Alert(
            alert_type="test", message="Ack me",
            severity=AlertSeverity.MEDIUM, status=AlertStatus.GENERATED,
        )
        session.add(alert)
        session.flush()
        acknowledge_alert(session, alert.id)
        session.expire(alert)
        assert alert.status == AlertStatus.ACKNOWLEDGED

    def test_snooze_alert(self, session):
        from mihomes.services.alerts import snooze_alert
        alert = Alert(
            alert_type="test", message="Snooze me",
            severity=AlertSeverity.LOW, status=AlertStatus.GENERATED,
        )
        session.add(alert)
        session.flush()
        snooze_alert(session, alert.id, days=3)
        session.expire(alert)
        assert alert.status == AlertStatus.ACKNOWLEDGED
        assert alert.snoozed_until is not None

    def test_list_excludes_resolved(self, session):
        from mihomes.services.alerts import list_alerts
        resolved = Alert(
            alert_type="test", message="Resolved alert",
            severity=AlertSeverity.LOW, status=AlertStatus.RESOLVED,
        )
        session.add(resolved)
        session.flush()
        actives = list_alerts(session)
        assert not any(a.message == "Resolved alert" for a in actives)
