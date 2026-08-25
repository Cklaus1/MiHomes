"""Work order service — CRUD with state machine transitions."""

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from mihomes.models.issue import Issue, IssueStatus
from mihomes.models.property import Property
from mihomes.models.staff import Staff
from mihomes.models.vendor import Vendor
from mihomes.models.work_order import WorkOrder, WorkOrderStatus
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier
from mihomes.services.update_helpers import safe_update

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


def _require_scheduling_entitlement(session: Session, due_date) -> None:
    """Gate `work_order_scheduling` — **and D13 scopes that key to exactly one capability**.

    *"Setting `WorkOrder.due_date`."* Not `assignee_id`, not `Appointment`/`/calendar`. The key
    named a feature that did not exist (F5), so enforcing it required defining it first, and the
    scope matters more than the gate:

    - `assignee_id` is not coherently gateable — the web UI exposes no assignee field at all, so
      a gate would fire only on the CLI, which SPEC-002 D1 makes an operator tool.
    - `Appointment`/`/calendar` is a **different product wearing the same word**. Gating it would
      paywall the whole calendar, the Telegram bot's appointment creation, and
      `services/recurring.py`'s nightly automation — far more than `PRICING:27` sells, and it
      would present to a Free user as a broken background job rather than an upgrade prompt (N9).

    **No due date, no gate.** A work order without one is allowed on every plan, which is what
    keeps the Telegram path unaffected (F5: `responder.py` passes no `due_date`) and satisfies
    N10 — a gate that tripped a bot message would deny a user something they did not choose to do.
    """
    if due_date is None:
        return

    from mihomes.entitlements.limits import UPGRADE_PATH
    from mihomes.entitlements.service import Denied, can
    from mihomes.models.account import Account
    from mihomes.services.property import EntitlementError
    from mihomes.tenancy import current_account

    account_id = current_account.get(None)
    if account_id is None:
        return  # operator CLI / background job (SPEC-002 D1)

    account = session.get(Account, account_id)
    if account is None:  # pragma: no cover - a bound account always exists
        return

    if isinstance(can(account, "work_order.schedule"), Denied):
        raise EntitlementError(
            Denied(
                reason=(
                    f"Scheduling work orders is not included in the {account.plan} plan. "
                    "You can still create this work order without a due date."
                ),
                upgrade_target=UPGRADE_PATH.get(getattr(account, "plan", "free")),
                limit=False,
            )
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
    _require_scheduling_entitlement(session, due_date)
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
    # H23: converge the issue↔WO link on `issue_id`. The CLI passes the link as
    # source_type="issue"/source_id; the web layer sets issue_id directly. Mirror
    # an issue source into issue_id so both list_work_orders_by_issue() and
    # verify() (which now read issue_id) see every issue-sourced work order.
    issue_id = source_id if source_type == "issue" else None
    wo = WorkOrder(
        title=title, slug=slug, description=description,
        property_id=prop.id, source_type=source_type, source_id=source_id,
        issue_id=issue_id,
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


def list_work_orders_by_issue(session: Session, issue_id: int) -> list[WorkOrder]:
    return session.query(WorkOrder).filter(WorkOrder.issue_id == issue_id).order_by(WorkOrder.created_at.desc()).all()


def get_work_order(session: Session, id_or_slug: str) -> WorkOrder:
    return resolve_identifier(session, WorkOrder, id_or_slug)


def update_work_order(session: Session, id_or_slug: str, **kwargs) -> WorkOrder:
    # Only when a due date is actually being *set* — an edit that leaves it alone, or clears it,
    # is not scheduling and must stay available on every plan.
    _require_scheduling_entitlement(session, kwargs.get("due_date"))
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

    if wo.status in (WorkOrderStatus.COMPLETED, WorkOrderStatus.VERIFIED):
        _resync_transaction(session, wo)

    return wo


def _resync_transaction(session: Session, wo: WorkOrder) -> None:
    """Keep a completed work order's budget transaction in sync with its current cost/vendor.

    A work order's transaction is otherwise a one-time snapshot taken at
    completion — without this, editing the cost or vendor afterward would
    silently leave the two permanently diverged.
    """
    from mihomes.models.budget import Transaction

    tx = session.query(Transaction).filter(
        Transaction.source == "work_order",
        Transaction.work_order_id == wo.id,
    ).first()
    if tx is None:
        return
    # M2: use actual_cost when it is set even if 0.0 (warranty/$0 work); a
    # bare `or` would discard 0.0 and book a phantom estimate instead.
    cost = wo.actual_cost if wo.actual_cost is not None else wo.estimated_cost
    if cost is None:
        return
    old_tx_snap = snapshot_instance(tx)
    safe_update(tx, {
        "amount": cost,
        "vendor_id": wo.vendor_id,
        "vendor_name": wo.vendor_name if not wo.vendor_id else None,
        "description": f"Work order: {wo.title}",
    })
    session.flush()
    tx_changes = diff_instance(old_tx_snap, snapshot_instance(tx))
    if tx_changes:
        record_change(session, "transaction", tx.id, "resync", tx_changes)


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

    # H22: resolve and validate the cost BEFORE mutating the work order. The old
    # order set status=COMPLETED + completed_at, flushed, and only then raised on
    # a missing cost — leaving the WO wedged in COMPLETED with no transaction.
    # M2: use actual_cost when it is set even if 0.0 (warranty/$0 work); a bare
    # `or` would discard 0.0 and book a phantom estimate instead.
    effective_cost = actual_cost if actual_cost is not None else wo.actual_cost
    cost = effective_cost if effective_cost is not None else wo.estimated_cost
    if cost is None:
        raise ValueError("Cannot complete work order without estimated or actual cost. Provide --actual-cost.")

    old_snap = snapshot_instance(wo)
    wo.status = WorkOrderStatus.COMPLETED
    wo.completed_at = datetime.now(timezone.utc)
    if actual_cost is not None:
        wo.actual_cost = actual_cost
    if notes:
        wo.completion_notes = notes
    session.flush()

    # Create a budget transaction for the completed work.
    if cost > 0:
        from mihomes.services.budget import add_transaction
        add_transaction(
            session, cost, str(wo.property_id), "maintenance",
            date.today(), vendor_id_or_slug=str(wo.vendor_id) if wo.vendor_id else None,
            vendor_name=wo.vendor_name if not wo.vendor_id else None,
            description=f"Work order: {wo.title}", source="work_order",
            work_order_id=wo.id,
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

    # If sourced from an issue, update the issue status to VERIFIED.
    # H23: read the converged `issue_id` link (falling back to the legacy
    # source_type/source_id pair for any rows created before convergence).
    linked_issue_id = wo.issue_id or (wo.source_id if wo.source_type == "issue" else None)
    if linked_issue_id:
        issue = session.get(Issue, linked_issue_id)
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
