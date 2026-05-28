"""Dashboard — portfolio overview."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from mihomes.models.alert import AlertStatus
from mihomes.models.work_order import WorkOrder, WorkOrderStatus
from mihomes.services import alerts as alert_svc
from mihomes.services import property as prop_svc
from mihomes.services import task as task_svc
from mihomes.services.health_score import compute_all_health_scores
from mihomes.web.deps import get_db, templates

router = APIRouter()


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    properties = prop_svc.list_properties(db)
    property_ids = [p.id for p in properties]
    health_scores = compute_all_health_scores(db, property_ids) if property_ids else {}
    overdue = task_svc.get_overdue_tasks(db)
    upcoming = task_svc.get_upcoming_tasks(db, days=7)
    all_alerts = alert_svc.list_alerts(db, status=AlertStatus.GENERATED)

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

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "page": "dashboard",
            "properties": properties,
            "health_scores": health_scores,
            "overdue_tasks": overdue,
            "upcoming_tasks": upcoming[:10],
            "alerts": all_alerts[:5],
            "total_alerts": len(all_alerts),
            "active_work_orders": active_work_orders,
            "open_wo_count": open_wo_count,
        },
    )
