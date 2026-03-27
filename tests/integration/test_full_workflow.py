"""Integration tests — exercise real service paths that have 0% coverage.

These tests find bugs by actually running the code paths, not reading them.
"""

import pytest
from datetime import date, timedelta

from mihomes.models import Base
from mihomes.models.property import Property, PropertyType, PropertyStatus
from mihomes.models.staff import Staff, StaffRole
from mihomes.models.vendor import Vendor
from mihomes.models.task import Task, TaskPriority, TaskStatus, RecurrenceFrequency, TaskSchedule
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.budget import Budget, BudgetPeriod, Transaction
from mihomes.models.asset import Asset, AssetType
from mihomes.models.work_order import WorkOrder, WorkOrderStatus
from mihomes.models.template import Template
from mihomes.models.note import Note
from mihomes.models.tag import Tag, TagAssignment
from mihomes.models.alert import Alert
from mihomes.models.configuration import Configuration
from mihomes.models.event import Event, Guest, EventGuest
from mihomes.models.document import Document, DocumentType
from mihomes.models.contract import Contract
from mihomes.models.insurance import InsurancePolicy, InsuranceType
from mihomes.models.recurring_expense import RecurringExpense, ExpenseFrequency
from mihomes.models.audit_log import AuditLog


# ---- Property CRUD ----

class TestPropertyService:
    def test_create_and_get(self, session):
        from mihomes.services.property import create_property, get_property
        p = create_property(session, "Test House", address="123 Main St", property_type=PropertyType.PRIMARY)
        assert p.id is not None
        assert p.slug == "test-house"
        fetched = get_property(session, "test-house")
        assert fetched.id == p.id

    def test_update_property(self, session):
        from mihomes.services.property import create_property, update_property
        p = create_property(session, "Old Name")
        update_property(session, "old-name", name="New Name", sqft=3000)
        session.expire_all()
        assert p.name == "New Name"
        assert p.sqft == 3000
        assert p.slug == "new-name"

    def test_update_does_not_overwrite_relationships(self, session):
        from mihomes.services.property import create_property, update_property
        from mihomes.services.space import create_space
        p = create_property(session, "House With Spaces")
        create_space(session, "Kitchen", str(p.id), space_type="kitchen")
        create_space(session, "Bedroom", str(p.id), space_type="bedroom")
        assert len(p.spaces) == 2
        update_property(session, str(p.id), name="Renamed House")
        session.expire_all()
        assert len(p.spaces) == 2  # Spaces preserved

    def test_delete_with_children(self, session):
        from mihomes.services.property import create_property, delete_property
        from mihomes.services.task import create_task
        from mihomes.services.issue import create_issue
        p = create_property(session, "Doomed House")
        create_task(session, "Task 1", str(p.id))
        create_issue(session, "Issue 1", str(p.id), severity=IssueSeverity.LOW)
        delete_property(session, "doomed-house")
        assert session.query(Property).filter(Property.slug == "doomed-house").first() is None
        assert session.query(Task).filter(Task.property_id == p.id).count() == 0
        assert session.query(Issue).filter(Issue.property_id == p.id).count() == 0

    def test_occupy_and_vacate(self, session):
        from mihomes.services.property import create_property, occupy_property, vacate_property
        p = create_property(session, "Vacation Home")
        occupy_property(session, "vacation-home", date(2026, 6, 1), date(2026, 8, 31))
        session.expire_all()
        assert p.occupied is True
        assert p.occupied_since == date(2026, 6, 1)
        vacate_property(session, "vacation-home")
        session.expire_all()
        assert p.occupied is False

    def test_empty_name_rejected(self, session):
        from mihomes.services.property import create_property
        with pytest.raises(ValueError, match="cannot be empty"):
            create_property(session, "")

    def test_whitespace_name_rejected(self, session):
        from mihomes.services.property import create_property
        with pytest.raises(ValueError, match="cannot be empty"):
            create_property(session, "   ")


# ---- Task Recurrence Lifecycle ----

class TestTaskRecurrence:
    def _setup(self, session):
        from mihomes.services.property import create_property
        return create_property(session, "Rec House", climate_zone="northeast")

    def test_quarterly_creates_next(self, session):
        from mihomes.services.task import create_task, complete_task
        p = self._setup(session)
        t = create_task(session, "Clean Gutters", str(p.id), due_date=date(2026, 4, 1),
                        recurrence=RecurrenceFrequency.QUARTERLY)
        assert t.schedule is not None
        assert t.schedule.next_due == date(2026, 7, 1)

        completed = complete_task(session, str(t.id), notes="Done")
        assert completed.status == TaskStatus.COMPLETED

        next_task = session.query(Task).filter(
            Task.property_id == p.id, Task.status == TaskStatus.PENDING
        ).first()
        assert next_task is not None
        assert next_task.due_date == date(2026, 7, 1)
        assert next_task.schedule.frequency == RecurrenceFrequency.QUARTERLY

    def test_seasonal_spring_fall(self, session):
        from mihomes.services.task import create_task, complete_task
        p = self._setup(session)
        t = create_task(session, "Gutter Check", str(p.id), due_date=date(2026, 3, 1),
                        recurrence=RecurrenceFrequency.SEASONAL, season_spec="spring,fall")
        assert t.schedule.season_spec == "spring,fall"

        completed = complete_task(session, str(t.id))
        next_task = session.query(Task).filter(
            Task.property_id == p.id, Task.status == TaskStatus.PENDING
        ).first()
        assert next_task is not None
        assert next_task.due_date == date(2026, 9, 1)  # Fall for northeast

    def test_double_complete_rejected(self, session):
        from mihomes.services.task import create_task, complete_task
        p = self._setup(session)
        t = create_task(session, "One Time", str(p.id), due_date=date(2026, 5, 1))
        complete_task(session, str(t.id))
        with pytest.raises(ValueError, match="already completed"):
            complete_task(session, str(t.id))

    def test_invalid_season_rejected(self, session):
        from mihomes.services.task import create_task
        p = self._setup(session)
        with pytest.raises(ValueError, match="Invalid season"):
            create_task(session, "Bad", str(p.id), recurrence=RecurrenceFrequency.SEASONAL,
                        season_spec="monsoon")

    def test_seasonal_without_spec_rejected(self, session):
        from mihomes.services.task import create_task
        p = self._setup(session)
        with pytest.raises(ValueError, match="Season spec required"):
            create_task(session, "Bad", str(p.id), recurrence=RecurrenceFrequency.SEASONAL)

    def test_title_too_long_rejected(self, session):
        from mihomes.services.task import create_task
        p = self._setup(session)
        with pytest.raises(ValueError, match="too long"):
            create_task(session, "A" * 301, str(p.id))


# ---- Work Order State Machine ----

class TestWorkOrderLifecycle:
    def _setup(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.vendor import create_vendor
        from mihomes.services.issue import create_issue
        p = create_property(session, "WO House")
        v = create_vendor(session, "WO Vendor")
        i = create_issue(session, "Broken Thing", str(p.id), severity=IssueSeverity.HIGH)
        return p, v, i

    def test_full_lifecycle(self, session):
        from mihomes.services.work_order import create_work_order, approve, complete, verify
        p, v, i = self._setup(session)
        wo = create_work_order(session, "Fix it", str(p.id),
                               source_type="issue", source_id=i.id,
                               vendor_id_or_slug=str(v.id), estimated_cost=500)
        assert wo.status == WorkOrderStatus.DRAFT
        approve(session, str(wo.id))
        assert wo.status == WorkOrderStatus.APPROVED
        complete(session, str(wo.id), actual_cost=550, notes="Extra parts")
        assert wo.status == WorkOrderStatus.COMPLETED

        # Verify a transaction was created
        tx = session.query(Transaction).filter(Transaction.source == "work_order").first()
        assert tx is not None
        assert tx.amount == 550

        verify(session, str(wo.id))
        assert wo.status == WorkOrderStatus.VERIFIED

        # Source issue should be verified too
        session.expire(i)
        assert i.status == IssueStatus.VERIFIED

    def test_invalid_transition_rejected(self, session):
        from mihomes.services.work_order import create_work_order, complete
        p, v, i = self._setup(session)
        wo = create_work_order(session, "Fix", str(p.id), estimated_cost=100)
        with pytest.raises(ValueError, match="Cannot transition"):
            complete(session, str(wo.id), actual_cost=100)

    def test_complete_without_cost_rejected(self, session):
        from mihomes.services.work_order import create_work_order, approve, complete
        p, v, i = self._setup(session)
        wo = create_work_order(session, "Fix", str(p.id))
        approve(session, str(wo.id))
        with pytest.raises(ValueError, match="without estimated or actual cost"):
            complete(session, str(wo.id))


# ---- Budget & Transactions ----

class TestBudget:
    def _setup(self, session):
        from mihomes.services.property import create_property
        return create_property(session, "Budget House")

    def test_budget_report(self, session):
        from mihomes.services.budget import set_budget, add_transaction, get_budget_report
        p = self._setup(session)
        set_budget(session, str(p.id), "maintenance", BudgetPeriod.ANNUAL, 10000,
                   date(2026, 1, 1))
        add_transaction(session, 1500, str(p.id), "maintenance", date(2026, 2, 15))
        add_transaction(session, 500, str(p.id), "maintenance", date(2026, 3, 10))

        report = get_budget_report(session, str(p.id), date(2026, 1, 1), date(2027, 1, 1))
        assert len(report) == 1
        assert report[0]["category"] == "maintenance"
        assert report[0]["budgeted"] == 10000
        assert report[0]["spent"] == 2000
        assert report[0]["pct_used"] == 20.0

    def test_negative_budget_rejected(self, session):
        from mihomes.services.budget import set_budget
        p = self._setup(session)
        with pytest.raises(ValueError, match="negative"):
            set_budget(session, str(p.id), "test", BudgetPeriod.ANNUAL, -500, date(2026, 1, 1))

    def test_unbudgeted_spending(self, session):
        from mihomes.services.budget import add_transaction, get_budget_report
        p = self._setup(session)
        add_transaction(session, 300, str(p.id), "misc", date(2026, 3, 1))
        report = get_budget_report(session, str(p.id), date(2026, 1, 1), date(2027, 1, 1))
        assert len(report) == 1
        assert report[0]["budgeted"] == 0
        assert report[0]["spent"] == 300
        assert report[0]["pct_used"] == 100.0  # Fixed in review round 2


# ---- Notes (Polymorphic) ----

class TestNotes:
    def test_note_on_property(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.note import add_note, list_notes
        p = create_property(session, "Note House")
        n = add_note(session, "property:note-house", "Test note")
        assert n.entity_type == "property"
        assert n.entity_id == p.id
        notes = list_notes(session, "property:note-house")
        assert len(notes) == 1

    def test_note_on_unknown_entity_rejected(self, session):
        from mihomes.services.note import add_note
        with pytest.raises(ValueError, match="Unknown entity type"):
            add_note(session, "spaceship:1", "Alien note")

    def test_note_bad_format_rejected(self, session):
        from mihomes.services.note import add_note
        with pytest.raises(ValueError, match="Invalid entity reference"):
            add_note(session, "no-colon", "Bad ref")


# ---- Tags ----

class TestTags:
    def test_tag_lifecycle(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.tag import create_tag, apply_tag, get_tagged_entities
        p = create_property(session, "Tagged House")
        create_tag(session, "vip")
        count = apply_tag(session, "vip", ["property:tagged-house"])
        assert count == 1
        entities = get_tagged_entities(session, "vip")
        assert len(entities) == 1
        assert entities[0]["type"] == "property"

    def test_duplicate_tag_ignored(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.tag import create_tag, apply_tag
        p = create_property(session, "DupTag House")
        create_tag(session, "test")
        apply_tag(session, "test", ["property:duptag-house"])
        count = apply_tag(session, "test", ["property:duptag-house"])
        assert count == 0  # Already tagged


# ---- Audit Log ----

class TestAuditLog:
    def test_create_logs_audit(self, session):
        from mihomes.services.property import create_property
        create_property(session, "Audited House")
        logs = session.query(AuditLog).filter(AuditLog.entity_type == "property").all()
        assert len(logs) >= 1
        assert logs[0].action == "create"

    def test_update_logs_changes(self, session):
        from mihomes.services.property import create_property, update_property
        p = create_property(session, "Audit Update House")
        update_property(session, str(p.id), name="New Audit Name")
        logs = session.query(AuditLog).filter(
            AuditLog.entity_type == "property",
            AuditLog.action == "update",
        ).all()
        assert len(logs) >= 1
        assert "name" in logs[0].changes


# ---- Config ----

class TestConfig:
    def test_get_default(self, session):
        from mihomes.services.config_service import get_config
        assert get_config(session, "ai.provider", "claude") == "claude"

    def test_set_and_get(self, session):
        from mihomes.services.config_service import set_config, get_config
        set_config(session, "test.key", "test_value")
        assert get_config(session, "test.key") == "test_value"

    def test_reset(self, session):
        from mihomes.services.config_service import set_config, reset_config, get_config
        set_config(session, "ai.provider", "openai")
        reset_config(session, "ai.provider")
        assert get_config(session, "ai.provider") == "claude"  # Back to default


# ---- Search ----

class TestSearch:
    def test_global_search_finds_property(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.search import global_search
        create_property(session, "Searchable Beach House", address="Ocean Drive")
        results = global_search(session, "ocean")
        assert any(r["type"] == "property" and "Searchable" in r["name"] for r in results)

    def test_search_by_type(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.vendor import create_vendor
        from mihomes.services.search import global_search
        create_property(session, "Test Search Prop")
        create_vendor(session, "Test Search Vendor")
        results = global_search(session, "test search", entity_type="vendor")
        assert all(r["type"] == "vendor" for r in results)


# ---- Events ----

class TestEvents:
    def test_event_with_guest_invite(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.event import create_event, create_guest, invite_guest, update_rsvp
        p = create_property(session, "Party House")
        ev = create_event(session, "BBQ", str(p.id), event_date=date(2026, 7, 4))
        g = create_guest(session, "Bob Smith", dietary_preferences="vegetarian")
        eg = invite_guest(session, str(ev.id), str(g.id))
        assert eg.rsvp_status == "invited"
        update_rsvp(session, str(ev.id), str(g.id), "accepted")
        session.expire(eg)
        assert eg.rsvp_status == "accepted"

    def test_invalid_rsvp_rejected(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.event import create_event, create_guest, invite_guest, update_rsvp
        p = create_property(session, "RSVP House")
        ev = create_event(session, "Party", str(p.id), event_date=date(2026, 8, 1))
        g = create_guest(session, "Invalid RSVP Guest")
        invite_guest(session, str(ev.id), str(g.id))
        with pytest.raises(ValueError, match="Invalid RSVP status"):
            update_rsvp(session, str(ev.id), str(g.id), "pizza")


# ---- Validators ----

class TestValidators:
    def test_validate_name_strips_whitespace(self):
        from mihomes.services.validators import validate_name
        assert validate_name("  Hello  ") == "Hello"

    def test_validate_name_rejects_empty(self):
        from mihomes.services.validators import validate_name
        with pytest.raises(ValueError):
            validate_name("")

    def test_validate_positive_rejects_negative(self):
        from mihomes.services.validators import validate_positive_amount
        with pytest.raises(ValueError):
            validate_positive_amount(-100)

    def test_validate_positive_accepts_zero(self):
        from mihomes.services.validators import validate_positive_amount
        assert validate_positive_amount(0) == 0
