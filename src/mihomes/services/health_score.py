"""Property health score — 0-100 composite score per property.

Scoring deductions:
  Open issues:    CRITICAL -20, HIGH -12, MEDIUM -5, LOW -2
  Overdue tasks:  -4 each (max -20)
  Budget overrun: -6 per category >100% (max -18)
  Unacked alerts: CRITICAL/HIGH -5 each (max -10)

Score is clamped to [0, 100].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from mihomes.models.alert import Alert, AlertSeverity, AlertStatus
from mihomes.models.budget import Budget, Transaction
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.task import Task, TaskStatus


_ISSUE_DEDUCTIONS = {
    IssueSeverity.CRITICAL: 20,
    IssueSeverity.HIGH: 12,
    IssueSeverity.MEDIUM: 5,
    IssueSeverity.LOW: 2,
}

_ALERT_DEDUCTIONS = {
    AlertSeverity.CRITICAL: 5,
    AlertSeverity.HIGH: 5,
}


@dataclass
class HealthScore:
    property_id: int
    score: int
    grade: str
    color: str  # CSS color class
    breakdown: dict[str, int] = field(default_factory=dict)


def _grade(score: int) -> tuple[str, str]:
    if score >= 90:
        return "A", "ok"
    if score >= 75:
        return "B", "ok"
    if score >= 60:
        return "C", "warn"
    if score >= 40:
        return "D", "warn"
    return "F", "danger"


def compute_property_health(session: Session, property_id: int) -> HealthScore:
    """Compute health score for a single property."""
    score = 100
    breakdown: dict[str, int] = {}

    # ── Open issues ──────────────────────────────────────────────────────────
    open_issues = (
        session.query(Issue)
        .filter(
            Issue.property_id == property_id,
            Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
        )
        .all()
    )
    issue_deduction = sum(_ISSUE_DEDUCTIONS.get(i.severity, 0) for i in open_issues)
    if issue_deduction:
        breakdown["issues"] = -issue_deduction
        score -= issue_deduction

    # ── Overdue tasks ─────────────────────────────────────────────────────────
    overdue_count = (
        session.query(func.count(Task.id))
        .filter(
            Task.property_id == property_id,
            Task.due_date < date.today(),
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        )
        .scalar()
    ) or 0
    task_deduction = min(overdue_count * 4, 20)
    if task_deduction:
        breakdown["overdue_tasks"] = -task_deduction
        score -= task_deduction

    # ── Budget overrun ────────────────────────────────────────────────────────
    budgets = (
        session.query(Budget).filter(Budget.property_id == property_id).all()
    )
    budget_deduction = 0
    for b in budgets:
        spent = (
            session.query(func.sum(Transaction.amount))
            .filter(
                Transaction.property_id == property_id,
                Transaction.category == b.category,
            )
            .scalar()
        ) or 0.0
        if b.amount > 0 and spent > b.amount:
            budget_deduction += 6
    budget_deduction = min(budget_deduction, 18)
    if budget_deduction:
        breakdown["budget"] = -budget_deduction
        score -= budget_deduction

    # ── Unacknowledged high/critical alerts ───────────────────────────────────
    # H17: scope alert deductions to this property. Property-less alerts
    # (property_id IS NULL) are system-wide and count against every property.
    bad_alerts = (
        session.query(Alert)
        .filter(
            Alert.status.in_([AlertStatus.GENERATED, AlertStatus.SEEN]),
            Alert.severity.in_([AlertSeverity.CRITICAL, AlertSeverity.HIGH]),
            or_(Alert.property_id == property_id, Alert.property_id.is_(None)),
        )
        .all()
    )
    alert_deduction = min(
        sum(_ALERT_DEDUCTIONS.get(a.severity, 0) for a in bad_alerts), 10
    )
    if alert_deduction:
        breakdown["alerts"] = -alert_deduction
        score -= alert_deduction

    score = max(0, min(100, score))
    grade, color = _grade(score)
    return HealthScore(
        property_id=property_id,
        score=score,
        grade=grade,
        color=color,
        breakdown=breakdown,
    )


def compute_all_health_scores(
    session: Session, property_ids: list[int]
) -> dict[int, HealthScore]:
    """Compute health scores for a list of property IDs. Returns id → HealthScore."""
    return {pid: compute_property_health(session, pid) for pid in property_ids}
