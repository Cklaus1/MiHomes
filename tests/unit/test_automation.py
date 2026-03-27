"""Tests for automation service."""

from datetime import date, timedelta

from mihomes.models.property import Property, PropertyType
from mihomes.models.task import Task, TaskPriority, TaskStatus
from mihomes.services.automation import escalate_overdue_tasks, generate_daily_digest


class TestEscalateOverdueTasks:
    def _create_property(self, session):
        prop = Property(name="Test House", slug="test-house", property_type=PropertyType.PRIMARY)
        session.add(prop)
        session.flush()
        return prop

    def test_escalates_low_to_medium(self, session):
        prop = self._create_property(session)
        task = Task(
            title="Old task", slug="old-task", property_id=prop.id,
            priority=TaskPriority.LOW, due_date=date.today() - timedelta(days=10),
        )
        session.add(task)
        session.flush()

        count = escalate_overdue_tasks(session, escalate_after_days=7)
        assert count == 1
        assert task.priority == TaskPriority.MEDIUM

    def test_escalates_medium_to_high(self, session):
        prop = self._create_property(session)
        task = Task(
            title="Med task", slug="med-task", property_id=prop.id,
            priority=TaskPriority.MEDIUM, due_date=date.today() - timedelta(days=10),
        )
        session.add(task)
        session.flush()

        escalate_overdue_tasks(session, escalate_after_days=7)
        assert task.priority == TaskPriority.HIGH

    def test_does_not_escalate_urgent(self, session):
        prop = self._create_property(session)
        task = Task(
            title="Urgent task", slug="urgent-task", property_id=prop.id,
            priority=TaskPriority.URGENT, due_date=date.today() - timedelta(days=10),
        )
        session.add(task)
        session.flush()

        count = escalate_overdue_tasks(session, escalate_after_days=7)
        assert count == 0

    def test_does_not_escalate_recent(self, session):
        prop = self._create_property(session)
        task = Task(
            title="Recent task", slug="recent-task", property_id=prop.id,
            priority=TaskPriority.LOW, due_date=date.today() - timedelta(days=3),
        )
        session.add(task)
        session.flush()

        count = escalate_overdue_tasks(session, escalate_after_days=7)
        assert count == 0

    def test_does_not_escalate_completed(self, session):
        prop = self._create_property(session)
        task = Task(
            title="Done task", slug="done-task", property_id=prop.id,
            priority=TaskPriority.LOW, status=TaskStatus.COMPLETED,
            due_date=date.today() - timedelta(days=10),
        )
        session.add(task)
        session.flush()

        count = escalate_overdue_tasks(session, escalate_after_days=7)
        assert count == 0


class TestGenerateDigest:
    def test_empty_digest(self, session):
        digest = generate_daily_digest(session)
        assert digest["date"] == date.today().isoformat()
        assert digest["overdue_tasks"] == []
        assert digest["tasks_due_today"] == []
        assert digest["critical_issues"] == []
