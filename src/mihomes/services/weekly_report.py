"""Weekly report service — operational summary for EA review."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from mihomes.models.budget import Budget, BudgetPeriod, Transaction
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.models.property import Property
from mihomes.models.staff import Staff
from mihomes.models.task import Task, TaskPriority, TaskStatus
from mihomes.models.work_order import WorkOrder, WorkOrderStatus


def generate(session: Session, property_slug: str | None = None) -> dict:
    """
    Build a weekly operational report.

    Covers the past 7 days (done) and next 7 days (upcoming).
    Flags overdue tasks, critical issues, and over-budget properties.
    """
    today = date.today()
    week_ago = today - timedelta(days=7)
    week_ahead = today + timedelta(days=7)

    # Resolve property filter
    prop_filter_ids: list[int] | None = None
    if property_slug and property_slug != "all":
        prop = session.query(Property).filter(
            (Property.slug == property_slug) | (Property.name.ilike(f"%{property_slug}%"))
        ).first()
        if not prop:
            raise ValueError(f"Property not found: {property_slug}")
        prop_filter_ids = [prop.id]
    else:
        props = session.query(Property).order_by(Property.name).all()
        prop_filter_ids = [p.id for p in props]

    properties = session.query(Property).filter(Property.id.in_(prop_filter_ids)).order_by(Property.name).all()

    # ── Tasks completed this week ─────────────────────────────────────────────
    completed_tasks = (
        session.query(Task)
        .filter(
            Task.property_id.in_(prop_filter_ids),
            Task.status == TaskStatus.COMPLETED,
            Task.completed_at >= datetime.combine(week_ago, datetime.min.time(), tzinfo=timezone.utc),
        )
        .order_by(Task.completed_at.desc())
        .all()
    )

    # ── Tasks in progress ─────────────────────────────────────────────────────
    _priority_rank = case(
        (Task.priority == "urgent", 0),
        (Task.priority == "high", 1),
        (Task.priority == "medium", 2),
        (Task.priority == "low", 3),
        else_=99,
    )
    in_progress_tasks = (
        session.query(Task)
        .filter(
            Task.property_id.in_(prop_filter_ids),
            Task.status == TaskStatus.IN_PROGRESS,
        )
        .order_by(_priority_rank, Task.due_date)
        .all()
    )

    # ── Overdue tasks ─────────────────────────────────────────────────────────
    overdue_tasks = (
        session.query(Task)
        .filter(
            Task.property_id.in_(prop_filter_ids),
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            Task.due_date < today,
        )
        .order_by(Task.due_date)
        .all()
    )

    # ── Upcoming tasks (next 7 days) ──────────────────────────────────────────
    upcoming_tasks = (
        session.query(Task)
        .filter(
            Task.property_id.in_(prop_filter_ids),
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            Task.due_date >= today,
            Task.due_date <= week_ahead,
        )
        .order_by(Task.due_date, _priority_rank)
        .all()
    )

    # ── Issues opened this week ───────────────────────────────────────────────
    new_issues = (
        session.query(Issue)
        .filter(
            Issue.property_id.in_(prop_filter_ids),
            Issue.created_at >= datetime.combine(week_ago, datetime.min.time(), tzinfo=timezone.utc),
        )
        .order_by(Issue.severity, Issue.created_at.desc())
        .all()
    )

    # ── Issues resolved this week ─────────────────────────────────────────────
    resolved_issues = (
        session.query(Issue)
        .filter(
            Issue.property_id.in_(prop_filter_ids),
            Issue.status.in_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
            Issue.resolved_at >= datetime.combine(week_ago, datetime.min.time(), tzinfo=timezone.utc),
        )
        .order_by(Issue.resolved_at.desc())
        .all()
    )

    # ── Open issues (all) ─────────────────────────────────────────────────────
    open_issues = (
        session.query(Issue)
        .filter(
            Issue.property_id.in_(prop_filter_ids),
            Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
        )
        .order_by(Issue.severity, Issue.created_at)
        .all()
    )

    # ── Work orders opened/closed this week ───────────────────────────────────
    new_work_orders = (
        session.query(WorkOrder)
        .filter(
            WorkOrder.property_id.in_(prop_filter_ids),
            WorkOrder.created_at >= datetime.combine(week_ago, datetime.min.time(), tzinfo=timezone.utc),
        )
        .all()
    )

    completed_work_orders = (
        session.query(WorkOrder)
        .filter(
            WorkOrder.property_id.in_(prop_filter_ids),
            WorkOrder.status.in_([WorkOrderStatus.COMPLETED, WorkOrderStatus.VERIFIED]),
            WorkOrder.completed_at >= datetime.combine(week_ago, datetime.min.time(), tzinfo=timezone.utc),
        )
        .all()
    )

    # ── Budget snapshot (MTD) ─────────────────────────────────────────────────
    month_start = today.replace(day=1)
    budget_rows = []
    for prop in properties:
        prop_budgets = session.query(Budget).filter(Budget.property_id == prop.id).all()
        budgeted_mtd = sum(
            b.amount for b in prop_budgets
            if _budget_covers_month(b.period_start, b.period, month_start)
        )
        spent_mtd = (
            session.query(func.sum(Transaction.amount))
            .filter(
                Transaction.property_id == prop.id,
                Transaction.date >= month_start,
                Transaction.date <= today,
            )
            .scalar() or 0.0
        )
        spent_this_week = (
            session.query(func.sum(Transaction.amount))
            .filter(
                Transaction.property_id == prop.id,
                Transaction.date >= week_ago,
                Transaction.date <= today,
            )
            .scalar() or 0.0
        )
        variance = round(spent_mtd - budgeted_mtd, 2) if budgeted_mtd else None
        over_budget = budgeted_mtd > 0 and spent_mtd > budgeted_mtd
        budget_rows.append({
            "property": prop.name,
            "slug": prop.slug,
            "budgeted_mtd": round(budgeted_mtd, 2),
            "spent_mtd": round(spent_mtd, 2),
            "spent_this_week": round(spent_this_week, 2),
            "variance": variance,
            "pct_used": round(spent_mtd / budgeted_mtd * 100, 1) if budgeted_mtd else None,
            "over_budget": over_budget,
            "currency": prop.currency,
        })

    # ── Flags ─────────────────────────────────────────────────────────────────
    flags = []
    if overdue_tasks:
        flags.append(f"{len(overdue_tasks)} overdue task(s) — oldest: {overdue_tasks[0].title} ({overdue_tasks[0].due_date})")
    critical_open = [i for i in open_issues if i.severity == IssueSeverity.CRITICAL]
    if critical_open:
        flags.append(f"{len(critical_open)} CRITICAL issue(s) unresolved: {', '.join(i.title for i in critical_open[:3])}")
    high_open = [i for i in open_issues if i.severity == IssueSeverity.HIGH]
    if high_open:
        flags.append(f"{len(high_open)} high-severity issue(s) open")
    over_budget = [b for b in budget_rows if b["over_budget"]]
    for b in over_budget:
        flags.append(f"{b['property']} over budget MTD: spent {b['spent_mtd']:,.0f} vs {b['budgeted_mtd']:,.0f} ({b['currency']})")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"from": week_ago.isoformat(), "to": today.isoformat()},
        "properties": [{"name": p.name, "slug": p.slug} for p in properties],

        # Done
        "completed_tasks": _serialize_tasks(completed_tasks, session),
        "resolved_issues": _serialize_issues(resolved_issues, session),
        "completed_work_orders": [_serialize_wo(w) for w in completed_work_orders],

        # In flight
        "in_progress_tasks": _serialize_tasks(in_progress_tasks, session),
        "open_issues": _serialize_issues(open_issues, session),

        # Overdue
        "overdue_tasks": _serialize_tasks(overdue_tasks, session),

        # Next week
        "upcoming_tasks": _serialize_tasks(upcoming_tasks, session),
        "new_issues": _serialize_issues(new_issues, session),
        "new_work_orders": [_serialize_wo(w) for w in new_work_orders],

        # Budget
        "budget": budget_rows,

        # Flags
        "flags": flags,
    }


# ── Serializers ───────────────────────────────────────────────────────────────

def _property_name(session: Session, property_id: int) -> str:
    p = session.get(Property, property_id)
    return p.name if p else str(property_id)


def _assignee_name(session: Session, assignee_id: int | None) -> str | None:
    if not assignee_id:
        return None
    s = session.get(Staff, assignee_id)
    return s.full_name if s else None


def _serialize_tasks(tasks: list[Task], session: Session) -> list[dict]:
    return [
        {
            "id": t.id,
            "title": t.title,
            "property": _property_name(session, t.property_id),
            "priority": t.priority.value,
            "status": t.status.value,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "completed_at": t.completed_at.date().isoformat() if t.completed_at else None,
            "assignee": _assignee_name(session, t.assignee_id),
        }
        for t in tasks
    ]


def _serialize_issues(issues: list[Issue], session: Session) -> list[dict]:
    return [
        {
            "id": i.id,
            "title": i.title,
            "property": _property_name(session, i.property_id),
            "severity": i.severity.value,
            "status": i.status.value,
            "created_at": i.created_at.date().isoformat() if i.created_at else None,
            "resolved_at": i.resolved_at.date().isoformat() if i.resolved_at else None,
        }
        for i in issues
    ]


def _serialize_wo(wo: WorkOrder) -> dict:
    return {
        "id": wo.id,
        "title": wo.title,
        "status": wo.status.value,
    }


def _budget_covers_month(period_start: date, period: BudgetPeriod, month_start: date) -> bool:
    """True if this budget period includes the month identified by month_start.

    Uses months_elapsed so monthly/quarterly/annual budgets are all handled:
    - MONTHLY: covers only its own month (months_elapsed == 0)
    - QUARTERLY: covers 3 months from period_start
    - ANNUAL: covers 12 months from period_start
    """
    months_elapsed = (month_start.year - period_start.year) * 12 + (month_start.month - period_start.month)
    if months_elapsed < 0:
        return False  # budget hasn't started yet
    if period == BudgetPeriod.MONTHLY:
        return months_elapsed == 0
    elif period == BudgetPeriod.QUARTERLY:
        return months_elapsed < 3
    else:  # ANNUAL
        return months_elapsed < 12
