"""Dashboard — portfolio overview."""

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.authz.scope import current_role
from mihomes.models.alert import AlertStatus
from mihomes.models.issue import IssueSeverity
from mihomes.models.work_order import WorkOrder, WorkOrderStatus
from mihomes.services import alerts as alert_svc
from mihomes.services import issue as issue_svc
from mihomes.services import onboarding_service as onboarding
from mihomes.services import property as prop_svc
from mihomes.services import task as task_svc
from mihomes.services.health_score import compute_all_health_scores
from mihomes.services.today import today_items
from mihomes.tenancy import current_account, current_user
from mihomes.web.deps import get_db, templates

router = APIRouter()

_SEVERITY_RANK = {
    IssueSeverity.CRITICAL: 0, IssueSeverity.HIGH: 1, IssueSeverity.MEDIUM: 2, IssueSeverity.LOW: 3,
}

#: Alert types whose subject already has its own dashboard card (overdue tasks are in Today).
_SHOWN_AS_OWN_CARD = frozenset({"overdue_task"})

#: Rows in the Today card; the rest are behind its "more" link.
_TODAY_ROWS = 8


# The portfolio overview is a collection view: it has no single target property, and for staff it
# must render *their* properties rather than 403 (N5). `property.view` is row 1 of the matrix,
# `SCOPED` for staff — the enforcement arrives at Step 7, which constrains the queries below by
# `scoped_property_ids()`. Declaring it now is what lets Step 4's harness verify coverage before
# any of that exists.
@router.get("/")
@declares("property.view", Access.COLLECTION)
def dashboard(request: Request, db: Session = Depends(get_db)):
    properties = prop_svc.list_properties(db)
    property_ids = [p.id for p in properties]
    health_scores = compute_all_health_scores(db, property_ids) if property_ids else {}
    # The viewer's own tasks: assigned to them, plus unassigned ones for owners/admins
    # (`task_svc.viewer_task_filter`). The Overdue Tasks tile and the Today list both count only
    # these, and the tile links to the same "My tasks" filter on the Tasks page.
    is_manager = current_role.get() in ("owner", "admin")
    viewer = current_user.get(None)
    is_mine = task_svc.viewer_task_filter(db, viewer, include_unassigned=is_manager)
    overdue = [t for t in task_svc.get_overdue_tasks(db) if is_mine(t)]
    today = today_items(db, user_id=viewer, include_unassigned=is_manager)
    upcoming = task_svc.get_upcoming_tasks(db, days=7)
    # Overdue tasks have their own card and tile, so their alerts are left out of the dashboard's
    # Active Alerts. Since `jobs alerts` runs hourly, every overdue task also has an alert, and
    # the dashboard listed each one twice. The Alerts page still lists them, to dismiss or snooze.
    all_alerts = [
        a for a in alert_svc.list_alerts(db, status=AlertStatus.GENERATED)
        if a.alert_type not in _SHOWN_AS_OWN_CARD
    ]

    active_work_orders = (
        db.query(WorkOrder)
        .filter(WorkOrder.status.in_([WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS]))
        .order_by(WorkOrder.created_at.desc())
        .limit(5)
        .all()
    )
    open_wo_count = (
        db.query(WorkOrder)
        .filter(WorkOrder.status.notin_([
            WorkOrderStatus.COMPLETED, WorkOrderStatus.VERIFIED, WorkOrderStatus.CANCELLED,
        ]))
        .count()
    )

    # Open issues, worst first. Ranked here rather than trusting the query's ORDER BY on the
    # enum column, which sorts by however the column happens to be stored.
    open_issues = issue_svc.list_issues(db, open_only=True)
    open_issues.sort(key=lambda i: _SEVERITY_RANK.get(i.severity, 9))

    # The "Getting started" card is for whoever sets the estate up. Staff see a property-scoped
    # slice of the account, so its counts would be wrong for them — and it is not their job.
    getting_started = None
    if is_manager:
        getting_started = onboarding.checklist(db, current_account.get())

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "page": "dashboard",
            "getting_started": getting_started,
            "properties": properties,
            "health_scores": health_scores,
            "overdue_tasks": overdue,
            "today_date": date.today(),
            "today_items": today[:_TODAY_ROWS],
            "today_more": max(0, len(today) - _TODAY_ROWS),
            "upcoming_tasks": upcoming[:10],
            # The tile shows the true count; the list above is capped.
            "upcoming_count": len(upcoming),
            "open_issues": open_issues[:5],
            "open_issue_count": len(open_issues),
            "alerts": all_alerts[:5],
            "total_alerts": len(all_alerts),
            "active_work_orders": active_work_orders,
            "open_wo_count": open_wo_count,
        },
    )


# Dismissing the "Getting started" card is estate set-up, so it is authorised as the people who
# set the estate up: `property.add` is owner/admin, staff denied — exactly who is shown the card.
# Deliberately *not* an onboarding (`Access.SESSION`) route: it acts on the session's bound
# account, which the matrix can authorise, and that exemption is pinned small for a reason.
@router.post("/getting-started/dismiss")
@declares("property.add", Access.ACCOUNT)
def dismiss_getting_started(request: Request, db: Session = Depends(get_db)):
    onboarding.dismiss_checklist(db, current_account.get())
    return RedirectResponse("/", status_code=303)
