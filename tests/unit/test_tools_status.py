"""Regression tests for the AI tools.py hardening pass (spec D3/H10/M32/M33).

Before the fix, every status filter was wrapped in ``except ValueError: pass``,
so an unrecognized status silently dropped the filter and returned *all* rows
presented as filtered; ``_query_issues`` referenced a non-existent
``IssueStatus.CLOSED`` (AttributeError on the default path); ``_query_budget``'s
default summary path ignored the property/category filter; and ``_query_alerts``
returned resolved/snoozed alerts. These pin the corrected behaviour.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from mihomes.models.alert import Alert, AlertSeverity, AlertStatus
from mihomes.models.budget import Transaction
from mihomes.models.issue import Issue, IssueStatus
from mihomes.models.property import Property, PropertyType
from mihomes.models.task import Task, TaskPriority, TaskStatus
from mihomes.services.ai.tools import (
    _query_alerts,
    _query_budget,
    _query_issues,
    _query_tasks,
    execute_tool,
)


@pytest.fixture
def db(session):
    return session


# --- D3: open-issues query no longer crashes on IssueStatus.CLOSED ---------

def test_query_issues_open_only_does_not_crash(db):
    prop = Property(name="Beach", slug="beach", property_type=PropertyType.PRIMARY)
    db.add(prop)
    db.flush()
    db.add(Issue(title="Roof leak", slug="roof-leak", property_id=prop.id, status=IssueStatus.REPORTED))
    db.add(Issue(title="Fixed pipe", slug="fixed-pipe", property_id=prop.id, status=IssueStatus.RESOLVED))
    db.flush()

    # Default path exercises the open_only branch — previously AttributeError.
    out = _query_issues(db, {})
    assert "Roof leak" in out
    assert "Fixed pipe" not in out


# --- H10: a bad status value surfaces an error, never a silent full list ----

def test_bad_task_status_returns_error_not_all_rows(db):
    prop = Property(name="Villa", slug="villa", property_type=PropertyType.PRIMARY)
    db.add(prop)
    db.flush()
    db.add(Task(title="Mow", slug="mow", property_id=prop.id,
                priority=TaskPriority.MEDIUM, status=TaskStatus.PENDING))
    db.flush()

    # 'done' is not a real value (real: completed) — must error, not list all.
    out = execute_tool(db, "query_tasks", {"status": "done"})
    assert "Invalid status" in out
    assert "Mow" not in out


def test_valid_hyphenated_task_status_filters(db):
    prop = Property(name="Villa", slug="villa", property_type=PropertyType.PRIMARY)
    db.add(prop)
    db.flush()
    db.add(Task(title="Active", slug="active", property_id=prop.id,
                priority=TaskPriority.MEDIUM, status=TaskStatus.IN_PROGRESS))
    db.add(Task(title="Waiting", slug="waiting", property_id=prop.id,
                priority=TaskPriority.MEDIUM, status=TaskStatus.PENDING))
    db.flush()

    out = _query_tasks(db, {"status": "in-progress"})
    assert "Active" in out
    assert "Waiting" not in out


def test_bad_issue_status_returns_error(db):
    prop = Property(name="Beach", slug="beach", property_type=PropertyType.PRIMARY)
    db.add(prop)
    db.flush()
    db.add(Issue(title="Roof leak", slug="roof-leak", property_id=prop.id, status=IssueStatus.REPORTED))
    db.flush()

    out = execute_tool(db, "query_issues", {"status": "closed"})
    assert "Invalid status" in out
    assert "Roof leak" not in out


# --- M32: default summary path honours the property filter ------------------

def test_budget_summary_respects_property_filter(db):
    a = Property(name="Beach House", slug="beach-house", property_type=PropertyType.PRIMARY)
    b = Property(name="Mountain", slug="mountain", property_type=PropertyType.VACATION)
    db.add_all([a, b])
    db.flush()
    yr = date.today().year
    db.add(Transaction(property_id=a.id, amount=100.0, category="Repairs", date=date(yr, 3, 1), description="a"))
    db.add(Transaction(property_id=b.id, amount=9999.0, category="Repairs", date=date(yr, 3, 1), description="b"))
    db.flush()

    # Default path is summary_only=True — must scope to Beach House only.
    out = _query_budget(db, {"property_slug": "beach-house"})
    assert "100" in out
    assert "9,999" not in out and "9999" not in out


# --- M33: alerts query excludes resolved / snoozed by default ---------------

def test_query_alerts_excludes_resolved_and_snoozed(db):
    db.add(Alert(alert_type="test", severity=AlertSeverity.HIGH, message="Active one",
                 status=AlertStatus.GENERATED))
    db.add(Alert(alert_type="test", severity=AlertSeverity.HIGH, message="Done one",
                 status=AlertStatus.RESOLVED))
    future = datetime.now(timezone.utc) + timedelta(days=3)
    db.add(Alert(alert_type="test", severity=AlertSeverity.HIGH, message="Snoozed one",
                 status=AlertStatus.GENERATED, snoozed_until=future))
    db.flush()

    out = _query_alerts(db, {})
    assert "Active one" in out
    assert "Done one" not in out
    assert "Snoozed one" not in out
