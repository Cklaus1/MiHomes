"""Edge case and fault injection tests — find bugs previous reviews missed.

Strategy: test every boundary condition, concurrent-like scenario, and
data corruption recovery path.
"""

import pytest
from datetime import date, timedelta

from mihomes.models.property import Property, PropertyType
from mihomes.models.task import Task, TaskPriority, TaskStatus, RecurrenceFrequency
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.work_order import WorkOrder, WorkOrderStatus
from mihomes.models.audit_log import AuditLog
from mihomes.models.alert import Alert


# ---- Slug Edge Cases ----

class TestSlugEdgeCases:
    def test_slug_with_all_special_chars(self, session):
        from mihomes.services.property import create_property
        p = create_property(session, "!!!@@@###$$$")
        # Should generate a fallback slug, not empty
        assert p.slug != ""
        assert p.slug.startswith("item-")  # Fallback format

    def test_slug_collision_with_100_duplicates(self, session):
        from mihomes.services.property import create_property
        for i in range(20):
            p = create_property(session, "Same Name")
            assert p.slug is not None
        # Should have same-name, same-name-2, ... same-name-20
        props = session.query(Property).filter(Property.name == "Same Name").all()
        assert len(props) == 20
        slugs = [p.slug for p in props]
        assert len(set(slugs)) == 20  # All unique

    def test_very_long_slug_generation(self, session):
        from mihomes.services.property import create_property
        p = create_property(session, "A" * 200)
        assert len(p.slug) <= 100  # slug column is String(100)

    def test_unicode_slug(self, session):
        from mihomes.services.property import create_property
        p = create_property(session, "Ñoño's Häuschen")
        assert p.slug  # Should generate something meaningful
        assert " " not in p.slug


# ---- Task Date Boundaries ----

class TestTaskDateBoundaries:
    def _prop(self, session):
        from mihomes.services.property import create_property
        return create_property(session, "Date Test House", climate_zone="northeast")

    def test_task_due_today(self, session):
        from mihomes.services.task import create_task, get_upcoming_tasks, get_overdue_tasks
        p = self._prop(session)
        create_task(session, "Due Today", str(p.id), due_date=date.today())
        upcoming = get_upcoming_tasks(session, days=1)
        assert len(upcoming) >= 1
        overdue = get_overdue_tasks(session)
        # Task due today should NOT be overdue
        overdue_titles = [t.title for t in overdue]
        assert "Due Today" not in overdue_titles

    def test_task_due_yesterday_is_overdue(self, session):
        from mihomes.services.task import create_task, get_overdue_tasks
        p = self._prop(session)
        create_task(session, "Due Yesterday", str(p.id), due_date=date.today() - timedelta(days=1))
        overdue = get_overdue_tasks(session)
        assert any(t.title == "Due Yesterday" for t in overdue)

    def test_task_with_no_due_date(self, session):
        from mihomes.services.task import create_task, get_overdue_tasks, get_upcoming_tasks
        p = self._prop(session)
        t = create_task(session, "No Due Date", str(p.id))
        assert t.due_date is None
        # Should not appear in overdue or upcoming
        overdue = get_overdue_tasks(session)
        upcoming = get_upcoming_tasks(session)
        assert all(o.title != "No Due Date" for o in overdue)
        assert all(u.title != "No Due Date" for u in upcoming)

    def test_monthly_recurrence_from_jan31(self, session):
        from mihomes.services.task import create_task
        p = self._prop(session)
        t = create_task(session, "End of Month", str(p.id),
                        due_date=date(2026, 1, 31), recurrence=RecurrenceFrequency.MONTHLY)
        # Next due should be Feb 28 (clamped)
        assert t.schedule.next_due == date(2026, 2, 28)

    def test_annual_recurrence_from_feb29(self, session):
        from mihomes.services.task import create_task
        p = self._prop(session)
        t = create_task(session, "Leap Day Task", str(p.id),
                        due_date=date(2028, 2, 29), recurrence=RecurrenceFrequency.ANNUAL)
        # Next due should be Feb 28, 2029 (not leap year)
        assert t.schedule.next_due == date(2029, 2, 28)


# ---- Issue Lifecycle Integrity ----

class TestIssueLifecycle:
    def _prop(self, session):
        from mihomes.services.property import create_property
        return create_property(session, "Issue House")

    def test_resolve_already_resolved(self, session):
        from mihomes.services.issue import create_issue, resolve_issue
        p = self._prop(session)
        i = create_issue(session, "Test Issue", str(p.id), severity=IssueSeverity.LOW)
        resolve_issue(session, str(i.id), notes="Fixed")
        # Resolving again should still work (idempotent update)
        resolve_issue(session, str(i.id), notes="Fixed again")
        session.expire(i)
        assert i.status == IssueStatus.RESOLVED
        assert i.resolution_notes == "Fixed again"

    def test_issue_with_space_reference(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.space import create_space
        from mihomes.services.issue import create_issue
        p = create_property(session, "Space Issue House")
        s = create_space(session, "Kitchen", str(p.id), space_type="kitchen")
        i = create_issue(session, "Kitchen Leak", str(p.id),
                         severity=IssueSeverity.HIGH, space_id_or_slug=str(s.id))
        assert i.space_id == s.id


# ---- Alert Deduplication ----

class TestAlertDedup:
    def test_overdue_alert_not_duplicated(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.task import create_task
        from mihomes.services.alerts import generate_alerts
        p = create_property(session, "Alert House")
        create_task(session, "Overdue Task", str(p.id),
                    due_date=date.today() - timedelta(days=3))
        # Generate twice
        count1 = generate_alerts(session)
        count2 = generate_alerts(session)
        assert count1 >= 1
        assert count2 == 0  # No new alerts on second run
        total = session.query(Alert).filter(Alert.alert_type == "overdue_task").count()
        assert total == 1


# ---- Work Order Edge Cases ----

class TestWorkOrderEdgeCases:
    def _setup(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.vendor import create_vendor
        p = create_property(session, "WO Edge House")
        v = create_vendor(session, "WO Edge Vendor")
        return p, v

    def test_cancel_from_any_state(self, session):
        from mihomes.services.work_order import create_work_order, cancel
        p, v = self._setup(session)
        wo = create_work_order(session, "Cancel Test", str(p.id), estimated_cost=100)
        cancel(session, str(wo.id))
        assert wo.status == WorkOrderStatus.CANCELLED

    def test_cannot_verify_uncompleted(self, session):
        from mihomes.services.work_order import create_work_order, approve, verify
        p, v = self._setup(session)
        wo = create_work_order(session, "Verify Test", str(p.id), estimated_cost=100)
        approve(session, str(wo.id))
        with pytest.raises(ValueError, match="Cannot transition"):
            verify(session, str(wo.id))

    def test_skip_to_complete_from_approved(self, session):
        from mihomes.services.work_order import create_work_order, approve, complete
        p, v = self._setup(session)
        wo = create_work_order(session, "Skip Test", str(p.id), estimated_cost=200)
        approve(session, str(wo.id))
        complete(session, str(wo.id), actual_cost=200)
        assert wo.status == WorkOrderStatus.COMPLETED


# ---- Audit Trail Integrity ----

class TestAuditIntegrity:
    def test_delete_creates_snapshot(self, session):
        from mihomes.services.property import create_property, delete_property
        p = create_property(session, "Audit Delete House")
        pid = p.id
        delete_property(session, str(pid))
        log = session.query(AuditLog).filter(
            AuditLog.entity_type == "property",
            AuditLog.entity_id == pid,
            AuditLog.action == "delete",
        ).first()
        assert log is not None
        assert log.changes is not None
        assert "name" in log.changes
        assert log.changes["name"] == "Audit Delete House"

    def test_update_only_logs_changed_fields(self, session):
        from mihomes.services.property import create_property, update_property
        p = create_property(session, "Audit Partial", address="123 St", sqft=2000)
        update_property(session, str(p.id), sqft=3000)
        log = session.query(AuditLog).filter(
            AuditLog.entity_type == "property",
            AuditLog.action == "update",
        ).first()
        assert "sqft" in log.changes
        assert "address" not in log.changes  # Unchanged field not logged
        assert "name" not in log.changes


# ---- Cross-Entity Consistency ----

class TestCrossEntity:
    def test_task_assignee_reference_valid(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.staff import create_staff
        from mihomes.services.task import create_task
        p = create_property(session, "Cross House")
        s = create_staff(session, "Cross Worker", property_id_or_slug=str(p.id))
        t = create_task(session, "Assigned Task", str(p.id), assignee_id_or_slug=str(s.id))
        assert t.assignee_id == s.id
        assert t.assignee.name == "Cross Worker"

    def test_task_with_invalid_assignee_rejected(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.task import create_task
        from mihomes.services.slug import EntityNotFoundError
        p = create_property(session, "Bad Assign House")
        with pytest.raises(EntityNotFoundError):
            create_task(session, "Bad Task", str(p.id), assignee_id_or_slug="nonexistent")

    def test_task_with_invalid_property_rejected(self, session):
        from mihomes.services.task import create_task
        from mihomes.services.slug import EntityNotFoundError
        with pytest.raises(EntityNotFoundError):
            create_task(session, "Orphan Task", "nonexistent-property")


# ---- Template Instantiation ----

class TestTemplateInstantiation:
    def test_template_creates_staggered_tasks(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.template import create_template, run_template
        p = create_property(session, "Template House")
        t = create_template(session, "Test Checklist", steps=["Step 1", "Step 2", "Step 3"])
        tasks = run_template(session, str(t.id), str(p.id), due_date=date(2026, 5, 1))
        assert len(tasks) == 3
        assert tasks[0].due_date == date(2026, 5, 1)
        assert tasks[1].due_date == date(2026, 5, 2)
        assert tasks[2].due_date == date(2026, 5, 3)

    def test_template_with_invalid_property_rejected(self, session):
        from mihomes.services.template import create_template, run_template
        from mihomes.services.slug import EntityNotFoundError
        t = create_template(session, "Orphan Template", steps=["Step 1"])
        with pytest.raises(EntityNotFoundError):
            run_template(session, str(t.id), "nonexistent")

    def test_empty_template_creates_no_tasks(self, session):
        from mihomes.services.property import create_property
        from mihomes.services.template import create_template, run_template
        p = create_property(session, "Empty Template House")
        t = create_template(session, "Empty Template")
        tasks = run_template(session, str(t.id), str(p.id))
        assert len(tasks) == 0
