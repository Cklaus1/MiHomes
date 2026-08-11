"""Tests for alerts service — extending existing partial coverage."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from mihomes.models.alert import Alert, AlertSeverity, AlertStatus
from mihomes.models.consumable import Consumable, ConsumableStatus
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.property import Property, PropertyType
from mihomes.models.task import Task, TaskPriority, TaskStatus
from mihomes.services.alerts import (
    _check_critical_issues,
    _check_low_inventory,
    _check_overdue_tasks,
    acknowledge_alert,
    generate_alerts,
    list_alerts,
    snooze_alert,
)

# Placeholder ids for polymorphic entity_type/entity_id pairs. Distinct
# constants because several tests rely on two ids being DIFFERENT (filter by
# one, assert the other is excluded) — a single shared UUID would make those
# tests pass for the wrong reason. Were integers before SPEC-002 D2.
_ENTITY_1 = uuid.uuid4()


@pytest.fixture
def prop(session):
    p = Property(name="Alert House", slug="alert-house",
                 property_type=PropertyType.PRIMARY)
    session.add(p)
    session.flush()
    return p


def _make_overdue_task(session, prop, title, days_overdue=3, priority=TaskPriority.MEDIUM):
    task = Task(
        title=title, slug=title.lower().replace(" ", "-"),
        property_id=prop.id, priority=priority,
        due_date=date.today() - timedelta(days=days_overdue),
        status=TaskStatus.PENDING,
    )
    session.add(task)
    session.flush()
    return task


def _make_old_issue(session, prop, title, severity=IssueSeverity.HIGH, days_old=5):
    dt = datetime.now(timezone.utc) - timedelta(days=days_old)
    issue = Issue(
        title=title, slug=title.lower().replace(" ", "-"),
        property_id=prop.id, severity=severity,
        status=IssueStatus.REPORTED, created_at=dt,
    )
    session.add(issue)
    session.flush()
    return issue


class TestCheckOverdueTasks:
    def test_creates_alert_for_overdue_task(self, session, prop):
        _make_overdue_task(session, prop, "Old Pool Task", days_overdue=3)
        count = _check_overdue_tasks(session)
        assert count == 1

    def test_high_severity_for_8_plus_days_overdue(self, session, prop):
        _make_overdue_task(session, prop, "Very Old Task", days_overdue=10)
        _check_overdue_tasks(session)
        alert = session.query(Alert).filter(Alert.alert_type == "overdue_task").first()
        assert alert.severity == AlertSeverity.HIGH

    def test_medium_severity_for_less_than_8_days(self, session, prop):
        _make_overdue_task(session, prop, "Slightly Overdue", days_overdue=3)
        _check_overdue_tasks(session)
        alert = session.query(Alert).filter(Alert.alert_type == "overdue_task").first()
        assert alert.severity == AlertSeverity.MEDIUM

    def test_no_alert_for_completed_task(self, session, prop):
        task = Task(
            title="Done Task", slug="done-task",
            property_id=prop.id, priority=TaskPriority.MEDIUM,
            due_date=date.today() - timedelta(days=5),
            status=TaskStatus.COMPLETED,
        )
        session.add(task)
        session.flush()
        count = _check_overdue_tasks(session)
        assert count == 0

    def test_no_alert_for_cancelled_task(self, session, prop):
        task = Task(
            title="Cancelled Task", slug="cancelled-task",
            property_id=prop.id, priority=TaskPriority.LOW,
            due_date=date.today() - timedelta(days=5),
            status=TaskStatus.CANCELLED,
        )
        session.add(task)
        session.flush()
        count = _check_overdue_tasks(session)
        assert count == 0

    def test_does_not_duplicate_existing_alert(self, session, prop):
        _make_overdue_task(session, prop, "Dup Task", days_overdue=5)
        _check_overdue_tasks(session)
        count2 = _check_overdue_tasks(session)
        assert count2 == 0

    def test_no_overdue_tasks_returns_zero(self, session):
        count = _check_overdue_tasks(session)
        assert count == 0


class TestCheckCriticalIssues:
    def test_creates_alert_for_old_critical_issue(self, session, prop):
        _make_old_issue(session, prop, "Critical Issue", severity=IssueSeverity.CRITICAL, days_old=5)
        count = _check_critical_issues(session)
        assert count == 1

    def test_creates_alert_for_old_high_issue(self, session, prop):
        _make_old_issue(session, prop, "High Issue", severity=IssueSeverity.HIGH, days_old=5)
        count = _check_critical_issues(session)
        assert count == 1

    def test_no_alert_for_recent_issue(self, session, prop):
        _make_old_issue(session, prop, "New Issue", severity=IssueSeverity.CRITICAL, days_old=1)
        count = _check_critical_issues(session)
        assert count == 0

    def test_no_alert_for_medium_issue(self, session, prop):
        _make_old_issue(session, prop, "Medium Issue", severity=IssueSeverity.MEDIUM, days_old=5)
        count = _check_critical_issues(session)
        assert count == 0

    def test_no_duplicate_alert(self, session, prop):
        _make_old_issue(session, prop, "Old Issue", severity=IssueSeverity.HIGH, days_old=5)
        _check_critical_issues(session)
        count2 = _check_critical_issues(session)
        assert count2 == 0

    def test_no_alert_for_resolved_issue(self, session, prop):
        issue = _make_old_issue(session, prop, "Resolved Issue",
                                severity=IssueSeverity.CRITICAL, days_old=5)
        issue.status = IssueStatus.RESOLVED
        session.flush()
        count = _check_critical_issues(session)
        assert count == 0

    def test_critical_severity_map_to_high_alert(self, session, prop):
        _make_old_issue(session, prop, "Critical Alert Issue",
                        severity=IssueSeverity.CRITICAL, days_old=5)
        _check_critical_issues(session)
        alert = session.query(Alert).filter(Alert.alert_type == "unresolved_issue").first()
        assert alert.severity == AlertSeverity.HIGH

    def test_high_issue_maps_to_medium_alert(self, session, prop):
        _make_old_issue(session, prop, "High Alert Issue",
                        severity=IssueSeverity.HIGH, days_old=5)
        _check_critical_issues(session)
        alert = session.query(Alert).filter(Alert.alert_type == "unresolved_issue").first()
        assert alert.severity == AlertSeverity.MEDIUM


class TestCheckLowInventory:
    def _make_consumable(self, session, prop, name, status):
        slug = name.lower().replace(" ", "-")
        item = Consumable(name=name, slug=slug, property_id=prop.id, status=status)
        session.add(item)
        session.flush()
        return item

    def test_creates_alert_for_low_item(self, session, prop):
        self._make_consumable(session, prop, "Low Cleaner", ConsumableStatus.LOW)
        count = _check_low_inventory(session)
        assert count == 1

    def test_creates_alert_for_out_item(self, session, prop):
        self._make_consumable(session, prop, "Out Salt", ConsumableStatus.OUT)
        count = _check_low_inventory(session)
        assert count == 1

    def test_no_alert_for_ok_item(self, session, prop):
        self._make_consumable(session, prop, "OK Soap", ConsumableStatus.OK)
        count = _check_low_inventory(session)
        assert count == 0

    def test_out_item_gets_high_severity(self, session, prop):
        self._make_consumable(session, prop, "Empty Filters", ConsumableStatus.OUT)
        _check_low_inventory(session)
        alert = session.query(Alert).filter(Alert.alert_type == "low_inventory").first()
        assert alert.severity == AlertSeverity.HIGH

    def test_low_item_gets_medium_severity(self, session, prop):
        self._make_consumable(session, prop, "Low Bleach", ConsumableStatus.LOW)
        _check_low_inventory(session)
        alert = session.query(Alert).filter(Alert.alert_type == "low_inventory").first()
        assert alert.severity == AlertSeverity.MEDIUM

    def test_no_duplicate_alert(self, session, prop):
        self._make_consumable(session, prop, "Dup Item", ConsumableStatus.LOW)
        _check_low_inventory(session)
        count2 = _check_low_inventory(session)
        assert count2 == 0

    def test_auto_resolves_stale_alerts_when_restocked(self, session, prop):
        item = self._make_consumable(session, prop, "Was Low", ConsumableStatus.LOW)
        _check_low_inventory(session)
        # Now restock the item
        item.status = ConsumableStatus.OK
        session.flush()
        _check_low_inventory(session)
        alert = session.query(Alert).filter(
            Alert.alert_type == "low_inventory",
            Alert.source_entity_id == item.id,
        ).first()
        assert alert.status == AlertStatus.RESOLVED


class TestListAlerts:
    def _make_alert(self, session, alert_type="test_alert",
                    severity=AlertSeverity.MEDIUM, status=AlertStatus.GENERATED):
        alert = Alert(
            alert_type=alert_type,
            source_entity_type="test",
            source_entity_id=_ENTITY_1,
            severity=severity,
            message="Test alert",
            status=status,
        )
        session.add(alert)
        session.flush()
        return alert

    def test_returns_active_alerts_by_default(self, session):
        self._make_alert(session, status=AlertStatus.GENERATED)
        self._make_alert(session, status=AlertStatus.RESOLVED)
        alerts = list_alerts(session)
        assert all(a.status != AlertStatus.RESOLVED for a in alerts)

    def test_excludes_snoozed_by_default(self, session):
        alert = self._make_alert(session)
        alert.snoozed_until = datetime.now(timezone.utc) + timedelta(hours=2)
        session.flush()
        alerts = list_alerts(session)
        ids = [a.id for a in alerts]
        assert alert.id not in ids

    def test_includes_snoozed_when_flag_set(self, session):
        alert = self._make_alert(session)
        alert.snoozed_until = datetime.now(timezone.utc) + timedelta(hours=2)
        session.flush()
        alerts = list_alerts(session, include_snoozed=True)
        ids = [a.id for a in alerts]
        assert alert.id in ids

    def test_filter_by_status(self, session):
        self._make_alert(session, status=AlertStatus.GENERATED)
        self._make_alert(session, status=AlertStatus.ACKNOWLEDGED)
        acked = list_alerts(session, status=AlertStatus.ACKNOWLEDGED)
        assert all(a.status == AlertStatus.ACKNOWLEDGED for a in acked)

    def test_includes_past_snoozed(self, session):
        alert = self._make_alert(session)
        alert.snoozed_until = datetime.now(timezone.utc) - timedelta(hours=1)  # already expired
        session.flush()
        alerts = list_alerts(session)
        ids = [a.id for a in alerts]
        assert alert.id in ids


class TestSnoozeAlert:
    def test_snoozes_alert(self, session):
        alert = Alert(
            alert_type="test", source_entity_type="test", source_entity_id=_ENTITY_1,
            severity=AlertSeverity.MEDIUM, message="Test",
        )
        session.add(alert)
        session.flush()
        result = snooze_alert(session, alert.id, days=3)
        assert result.status == AlertStatus.ACKNOWLEDGED
        assert result.snoozed_until is not None

    def test_snooze_nonexistent_raises(self, session):
        with pytest.raises(ValueError, match="not found"):
            snooze_alert(session, uuid.uuid4(), days=1)

    def test_snooze_duration_correct(self, session):
        alert = Alert(
            alert_type="test", source_entity_type="test", source_entity_id=_ENTITY_1,
            severity=AlertSeverity.LOW, message="Test",
        )
        session.add(alert)
        session.flush()
        before = datetime.now(timezone.utc)
        snooze_alert(session, alert.id, days=7)
        expected = before + timedelta(days=7)
        diff = abs((alert.snoozed_until - expected).total_seconds())
        assert diff < 5


class TestAcknowledgeAlert:
    def test_acknowledges_alert(self, session):
        alert = Alert(
            alert_type="test", source_entity_type="test", source_entity_id=_ENTITY_1,
            severity=AlertSeverity.HIGH, message="Test",
        )
        session.add(alert)
        session.flush()
        result = acknowledge_alert(session, alert.id)
        assert result.status == AlertStatus.ACKNOWLEDGED

    def test_acknowledge_nonexistent_raises(self, session):
        with pytest.raises(ValueError, match="not found"):
            acknowledge_alert(session, uuid.uuid4())


class TestGenerateAlerts:
    def test_generate_alerts_runs_all_checks(self, session, prop):
        from unittest.mock import patch
        # generate_alerts calls multiple sub-checks + generate_expiration_alerts
        with patch("mihomes.services.alerts._check_overdue_tasks", return_value=2), \
             patch("mihomes.services.alerts._check_critical_issues", return_value=1), \
             patch("mihomes.services.alerts._check_low_inventory", return_value=0), \
             patch("mihomes.services.automation.generate_expiration_alerts", return_value=1):
            count = generate_alerts(session)
        assert count == 4

    def test_generate_on_empty_db_returns_zero(self, session):
        # No data — should return 0 for most checks
        # generate_expiration_alerts also returns 0
        count = generate_alerts(session)
        assert count == 0
