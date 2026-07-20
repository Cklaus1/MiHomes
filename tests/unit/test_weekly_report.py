"""Tests for weekly_report service."""

from datetime import date, datetime, timedelta, timezone

import pytest

from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.property import Property, PropertyType
from mihomes.models.task import Task, TaskPriority, TaskStatus
from mihomes.models.work_order import WorkOrder, WorkOrderStatus
from mihomes.services.weekly_report import generate


@pytest.fixture
def prop(session):
    p = Property(name="Report House", slug="report-house",
                 property_type=PropertyType.PRIMARY, currency="USD")
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def prop2(session):
    p = Property(name="Beach House", slug="beach-house",
                 property_type=PropertyType.VACATION, currency="USD")
    session.add(p)
    session.flush()
    return p


def _make_task(session, prop, title, status=TaskStatus.PENDING,
               priority=TaskPriority.MEDIUM, due_days=3, completed_at=None):
    due = date.today() + timedelta(days=due_days)
    task = Task(
        title=title, slug=title.lower().replace(" ", "-"),
        property_id=prop.id, status=status, priority=priority,
        due_date=due,
    )
    if completed_at:
        task.completed_at = completed_at
        task.status = TaskStatus.COMPLETED
    session.add(task)
    session.flush()
    return task


def _make_issue(session, prop, title, severity=IssueSeverity.MEDIUM,
                status=IssueStatus.REPORTED, resolved_at=None):
    now = datetime.now(timezone.utc)
    issue = Issue(
        title=title, slug=title.lower().replace(" ", "-"),
        property_id=prop.id, severity=severity, status=status,
        created_at=now,
    )
    if resolved_at:
        issue.resolved_at = resolved_at
    session.add(issue)
    session.flush()
    return issue


class TestGenerateBasicStructure:
    def test_returns_all_expected_keys(self, session, prop):
        report = generate(session)
        expected_keys = {
            "generated_at", "period", "properties",
            "completed_tasks", "resolved_issues", "completed_work_orders",
            "in_progress_tasks", "open_issues",
            "overdue_tasks", "upcoming_tasks",
            "new_issues", "new_work_orders", "budget", "flags",
        }
        assert expected_keys <= set(report.keys())

    def test_period_covers_past_7_days(self, session, prop):
        report = generate(session)
        period_from = date.fromisoformat(report["period"]["from"])
        period_to = date.fromisoformat(report["period"]["to"])
        assert period_to == date.today()
        assert (period_to - period_from).days == 7

    def test_properties_list_populated(self, session, prop):
        report = generate(session)
        slugs = [p["slug"] for p in report["properties"]]
        assert "report-house" in slugs

    def test_empty_db_produces_empty_lists(self, session, prop):
        report = generate(session)
        assert report["completed_tasks"] == []
        assert report["overdue_tasks"] == []
        assert report["open_issues"] == []

    def test_invalid_property_raises(self, session):
        with pytest.raises(ValueError, match="Property not found"):
            generate(session, property_slug="nonexistent-slug")


class TestTasksInReport:
    def test_overdue_task_appears_in_overdue(self, session, prop):
        _make_task(session, prop, "Overdue Task", due_days=-5)
        report = generate(session)
        titles = [t["title"] for t in report["overdue_tasks"]]
        assert "Overdue Task" in titles

    def test_upcoming_task_appears_in_upcoming(self, session, prop):
        _make_task(session, prop, "Upcoming Task", due_days=3)
        report = generate(session)
        titles = [t["title"] for t in report["upcoming_tasks"]]
        assert "Upcoming Task" in titles

    def test_completed_task_this_week_in_completed(self, session, prop):
        recent_completion = datetime.now(timezone.utc) - timedelta(days=2)
        _make_task(session, prop, "Done Task", completed_at=recent_completion)
        report = generate(session)
        titles = [t["title"] for t in report["completed_tasks"]]
        assert "Done Task" in titles

    def test_in_progress_task_appears(self, session, prop):
        _make_task(session, prop, "In Progress", status=TaskStatus.IN_PROGRESS, due_days=2)
        report = generate(session)
        titles = [t["title"] for t in report["in_progress_tasks"]]
        assert "In Progress" in titles

    def test_task_due_far_future_not_in_upcoming(self, session, prop):
        _make_task(session, prop, "Far Future", due_days=30)
        report = generate(session)
        titles = [t["title"] for t in report["upcoming_tasks"]]
        assert "Far Future" not in titles

    def test_task_serialized_with_expected_fields(self, session, prop):
        _make_task(session, prop, "Check HVAC", due_days=2)
        report = generate(session)
        task = next((t for t in report["upcoming_tasks"] if t["title"] == "Check HVAC"), None)
        assert task is not None
        assert "priority" in task
        assert "status" in task
        assert "due_date" in task
        assert "property" in task


class TestIssuesInReport:
    def test_open_issue_appears_in_open_issues(self, session, prop):
        _make_issue(session, prop, "Broken Window")
        report = generate(session)
        titles = [i["title"] for i in report["open_issues"]]
        assert "Broken Window" in titles

    def test_new_issue_this_week_in_new_issues(self, session, prop):
        _make_issue(session, prop, "Roof Leak")
        report = generate(session)
        titles = [i["title"] for i in report["new_issues"]]
        assert "Roof Leak" in titles

    def test_resolved_issue_this_week_in_resolved(self, session, prop):
        recent = datetime.now(timezone.utc) - timedelta(days=2)
        _make_issue(session, prop, "Fixed Pipe", status=IssueStatus.RESOLVED, resolved_at=recent)
        report = generate(session)
        titles = [i["title"] for i in report["resolved_issues"]]
        assert "Fixed Pipe" in titles

    def test_critical_issue_adds_flag(self, session, prop):
        _make_issue(session, prop, "Gas Leak!", severity=IssueSeverity.CRITICAL)
        report = generate(session)
        flags_text = " ".join(report["flags"])
        assert "CRITICAL" in flags_text

    def test_high_severity_issue_adds_flag(self, session, prop):
        _make_issue(session, prop, "Structural Problem", severity=IssueSeverity.HIGH)
        report = generate(session)
        flags_text = " ".join(report["flags"])
        assert "high-severity" in flags_text


class TestFlagsGeneration:
    def test_overdue_task_generates_flag(self, session, prop):
        _make_task(session, prop, "Overdue Flag Task", due_days=-10)
        report = generate(session)
        assert len(report["flags"]) >= 1
        assert any("overdue" in f.lower() for f in report["flags"])

    def test_no_flags_for_clean_estate(self, session, prop):
        # Just a future task — no flags
        _make_task(session, prop, "Fine Task", due_days=10)
        report = generate(session)
        assert report["flags"] == []


class TestPropertyFilter:
    def test_filter_by_property_slug(self, session, prop, prop2):
        _make_task(session, prop, "House1 Task", due_days=-2)
        _make_task(session, prop2, "Beach Task", due_days=-2)
        report = generate(session, property_slug="report-house")
        titles = [t["title"] for t in report["overdue_tasks"]]
        assert "House1 Task" in titles
        assert "Beach Task" not in titles

    def test_all_properties_when_no_filter(self, session, prop, prop2):
        _make_task(session, prop, "House1 Task", due_days=-2)
        _make_task(session, prop2, "Beach Task", due_days=-2)
        report = generate(session)
        titles = [t["title"] for t in report["overdue_tasks"]]
        assert "House1 Task" in titles
        assert "Beach Task" in titles


class TestBudgetSection:
    def test_budget_section_present_for_each_property(self, session, prop, prop2):
        report = generate(session)
        slugs = {b["slug"] for b in report["budget"]}
        assert "report-house" in slugs
        assert "beach-house" in slugs

    def test_no_budget_shows_zero(self, session, prop):
        report = generate(session, property_slug="report-house")
        budget = report["budget"][0]
        assert budget["budgeted_mtd"] == 0.0
        assert budget["spent_mtd"] == 0.0
