"""Work order service — CRUD with state machine transitions."""

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from mihomes.models.issue import Issue, IssueStatus
from mihomes.models.property import Property
from mihomes.models.staff import Staff
from mihomes.models.vendor import Vendor
from mihomes.models.work_order import WorkOrder, WorkOrderStatus
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.update_helpers import safe_update
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier

# Valid state transitions: {from_status: [allowed_to_statuses]}
VALID_TRANSITIONS: dict[WorkOrderStatus, list[WorkOrderStatus]] = {
    WorkOrderStatus.DRAFT: [WorkOrderStatus.ESTIMATED, WorkOrderStatus.APPROVED, WorkOrderStatus.CANCELLED],
    WorkOrderStatus.ESTIMATED: [WorkOrderStatus.APPROVED, WorkOrderStatus.CANCELLED],
    WorkOrderStatus.APPROVED: [WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS, WorkOrderStatus.COMPLETED, WorkOrderStatus.CANCELLED],
    WorkOrderStatus.ASSIGNED: [WorkOrderStatus.IN_PROGRESS, WorkOrderStatus.COMPLETED, WorkOrderStatus.CANCELLED],
    WorkOrderStatus.IN_PROGRESS: [WorkOrderStatus.COMPLETED, WorkOrderStatus.CANCELLED],
    WorkOrderStatus.COMPLETED: [WorkOrderStatus.VERIFIED],
    WorkOrderStatus.VERIFIED: [],
    WorkOrderStatus.CANCELLED: [],
}


def _validate_transition(current: WorkOrderStatus, target: WorkOrderStatus) -> None:
    allowed = VALID_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise ValueError(
            f"Cannot transition from {current.value} to {target.value}. "
            f"Allowed: {[s.value for s in allowed]}"
        )


def create_work_order(
    session: Session,
    title: str,
    property_id_or_slug: str,
    *,
    description: str | None = None,
    source_type: str | None = None,
    source_id: int | None = None,
    vendor_id_or_slug: str | None = None,
    vendor_name: str | None = None,
    assignee_id_or_slug: str | None = None,
    estimated_cost: float | None = None,
    currency: str = "USD",
    due_date: datetime | None = None,
    slug: str | None = None,
) -> WorkOrder:
    prop = resolve_identifier(session, Property, property_id_or_slug)
    vendor_id = None
    if vendor_id_or_slug:
        vendor = resolve_identifier(session, Vendor, vendor_id_or_slug)
        vendor_id = vendor.id
    assignee_id = None
    if assignee_id_or_slug:
        assignee = resolve_identifier(session, Staff, assignee_id_or_slug)
        assignee_id = assignee.id
    slug = ensure_unique_slug(session, WorkOrder, slug or generate_slug(title))
    wo = WorkOrder(
        title=title, slug=slug, description=description,
        property_id=prop.id, source_type=source_type, source_id=source_id,
        vendor_id=vendor_id, vendor_name=vendor_name, assignee_id=assignee_id,
        estimated_cost=estimated_cost, currency=currency, due_date=due_date,
    )
    session.add(wo)
    session.flush()
    record_change(session, "work_order", wo.id, "create", snapshot_instance(wo))
    return wo


def list_work_orders(
    session: Session,
    *,
    property_id_or_slug: str | None = None,
    status: WorkOrderStatus | None = None,
) -> list[WorkOrder]:
    query = session.query(WorkOrder)
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(WorkOrder.property_id == prop.id)
    if status:
        query = query.filter(WorkOrder.status == status)
    return query.order_by(WorkOrder.created_at.desc()).all()


def get_work_order(session: Session, id_or_slug: str) -> WorkOrder:
    return resolve_identifier(session, WorkOrder, id_or_slug)


def update_work_order(session: Session, id_or_slug: str, **kwargs) -> WorkOrder:
    wo = resolve_identifier(session, WorkOrder, id_or_slug)
    old_snap = snapshot_instance(wo)
    if "title" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, WorkOrder, generate_slug(kwargs["title"]), exclude_id=wo.id)
    safe_update(wo, kwargs)
    session.flush()
    new_snap = snapshot_instance(wo)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "work_order", wo.id, "update", changes)
    return wo


def transition_status(session: Session, id_or_slug: str, target: WorkOrderStatus) -> WorkOrder:
    """Transition a work order to a new status, validating the transition."""
    wo = resolve_identifier(session, WorkOrder, id_or_slug)
    _validate_transition(wo.status, target)
    old_snap = snapshot_instance(wo)
    wo.status = target
    session.flush()
    new_snap = snapshot_instance(wo)
    changes = diff_instance(old_snap, new_snap)
    record_change(session, "work_order", wo.id, "transition", changes)
    return wo


def approve(session: Session, id_or_slug: str) -> WorkOrder:
    """Approve a work order."""
    return transition_status(session, id_or_slug, WorkOrderStatus.APPROVED)


def complete(
    session: Session,
    id_or_slug: str,
    *,
    actual_cost: float | None = None,
    notes: str | None = None,
) -> WorkOrder:
    """Complete a work order and create a budget transaction."""
    wo = resolve_identifier(session, WorkOrder, id_or_slug)
    _validate_transition(wo.status, WorkOrderStatus.COMPLETED)
    old_snap = snapshot_instance(wo)
    wo.status = WorkOrderStatus.COMPLETED
    wo.completed_at = datetime.now(timezone.utc)
    if actual_cost is not None:
        wo.actual_cost = actual_cost
    if notes:
        wo.completion_notes = notes
    session.flush()

    # Create a budget transaction for the completed work
    cost = wo.actual_cost or wo.estimated_cost
    if cost is None:
        raise ValueError("Cannot complete work order without estimated or actual cost. Provide --actual-cost.")
    if cost > 0:
        from mihomes.services.budget import add_transaction
        add_transaction(
            session, cost, str(wo.property_id), "maintenance",
            date.today(), vendor_id_or_slug=str(wo.vendor_id) if wo.vendor_id else None,
            description=f"Work order: {wo.title}", source="work_order",
        )

    new_snap = snapshot_instance(wo)
    changes = diff_instance(old_snap, new_snap)
    record_change(session, "work_order", wo.id, "complete", changes)
    return wo


def verify(session: Session, id_or_slug: str) -> WorkOrder:
    """Verify a completed work order. If sourced from an issue, update issue status."""
    wo = resolve_identifier(session, WorkOrder, id_or_slug)
    _validate_transition(wo.status, WorkOrderStatus.VERIFIED)
    old_snap = snapshot_instance(wo)
    wo.status = WorkOrderStatus.VERIFIED
    wo.verified_at = datetime.now(timezone.utc)
    session.flush()

    # If sourced from an issue, update the issue status to VERIFIED
    if wo.source_type == "issue" and wo.source_id:
        issue = session.get(Issue, wo.source_id)
        if issue:
            old_issue_status = issue.status.value
            issue.status = IssueStatus.VERIFIED
            session.flush()
            record_change(session, "issue", issue.id, "update",
                          {"status": {"old": old_issue_status, "new": "verified"}})

    new_snap = snapshot_instance(wo)
    changes = diff_instance(old_snap, new_snap)
    record_change(session, "work_order", wo.id, "verify", changes)
    return wo


def cancel(session: Session, id_or_slug: str, notes: str | None = None) -> WorkOrder:
    """Cancel a work order from any non-terminal state."""
    wo = resolve_identifier(session, WorkOrder, id_or_slug)
    _validate_transition(wo.status, WorkOrderStatus.CANCELLED)
    old_snap = snapshot_instance(wo)
    wo.status = WorkOrderStatus.CANCELLED
    if notes:
        wo.completion_notes = notes
    session.flush()
    new_snap = snapshot_instance(wo)
    changes = diff_instance(old_snap, new_snap)
    record_change(session, "work_order", wo.id, "cancel", changes)
    return wo


def delete_work_order(session: Session, id_or_slug: str) -> str:
    wo = resolve_identifier(session, WorkOrder, id_or_slug)
    name = wo.title
    record_change(session, "work_order", wo.id, "delete", snapshot_instance(wo))
    session.delete(wo)
    session.flush()
    return name
