"""Dashboard service — aggregate data across the estate."""

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from mihomes.models.alert import Alert, AlertStatus
from mihomes.models.asset import Asset
from mihomes.models.audit_log import AuditLog
from mihomes.models.budget import Budget, Transaction
from mihomes.models.issue import Issue, IssueStatus
from mihomes.models.property import Property
from mihomes.models.task import Task, TaskStatus
from mihomes.models.work_order import WorkOrder, WorkOrderStatus


def get_dashboard_data(session: Session, property_id: int | None = None) -> dict:
    """Aggregate dashboard data. Optionally scoped to a single property."""
    today = date.today()
    year_start = date(today.year, 1, 1)
    year_end = date(today.year + 1, 1, 1)
    week_end = today + timedelta(days=7)

    # Properties
    prop_query = session.query(Property)
    if property_id:
        prop_query = prop_query.filter(Property.id == property_id)
    properties = prop_query.order_by(Property.name).all()

    prop_summaries = []
    for p in properties:
        open_issues = session.query(func.count(Issue.id)).filter(
            Issue.property_id == p.id,
            Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
        ).scalar()
        prop_summaries.append({
            "name": p.name, "slug": p.slug, "status": p.status.value,
            "type": p.property_type.value, "occupied": p.occupied,
            "occupied_since": p.occupied_since,
            "occupied_until": p.occupied_until,
            "open_issues": open_issues,
        })

    # Tasks due this week
    task_query = session.query(Task).filter(
        Task.due_date >= today, Task.due_date <= week_end,
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
    )
    if property_id:
        task_query = task_query.filter(Task.property_id == property_id)
    tasks_this_week = task_query.order_by(Task.due_date).all()

    # Overdue count
    overdue_query = session.query(func.count(Task.id)).filter(
        Task.due_date < today,
        Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
    )
    if property_id:
        overdue_query = overdue_query.filter(Task.property_id == property_id)
    overdue_count = overdue_query.scalar()

    # Open issues
    issue_query = session.query(Issue).filter(
        Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
    )
    if property_id:
        issue_query = issue_query.filter(Issue.property_id == property_id)
    open_issues = issue_query.order_by(Issue.severity).limit(10).all()

    # Budget summary per property
    budget_summaries = []
    for p in properties:
        budgeted = session.query(func.sum(Budget.amount)).filter(
            Budget.property_id == p.id,
            Budget.period_start >= year_start,
            Budget.period_start < year_end,
        ).scalar() or 0

        spent = session.query(func.sum(Transaction.amount)).filter(
            Transaction.property_id == p.id,
            Transaction.date >= year_start,
            Transaction.date < year_end,
        ).scalar() or 0

        if budgeted > 0:
            budget_summaries.append({
                "property": p.name, "budgeted": budgeted, "spent": spent,
                "pct_used": round(spent / budgeted * 100, 1),
                "currency": p.currency,
            })

    # Open work orders
    wo_query = session.query(WorkOrder).filter(
        WorkOrder.status.notin_([WorkOrderStatus.COMPLETED, WorkOrderStatus.VERIFIED, WorkOrderStatus.CANCELLED]),
    )
    if property_id:
        wo_query = wo_query.filter(WorkOrder.property_id == property_id)
    open_work_orders = wo_query.count()

    # Asset count
    asset_query = session.query(func.count(Asset.id)).filter(Asset.active.is_(True))
    if property_id:
        asset_query = asset_query.filter(Asset.property_id == property_id)
    asset_count = asset_query.scalar()

    # Alert count
    alert_count = session.query(func.count(Alert.id)).filter(
        Alert.status != AlertStatus.RESOLVED,
    ).scalar()

    return {
        "properties": prop_summaries,
        "tasks_this_week": tasks_this_week,
        "overdue_count": overdue_count,
        "open_issues": open_issues,
        "budget_summaries": budget_summaries,
        "open_work_orders": open_work_orders,
        "asset_count": asset_count,
        "alert_count": alert_count,
    }
