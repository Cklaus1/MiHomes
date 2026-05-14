"""Dashboard — portfolio overview."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from mihomes.models.alert import AlertStatus
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
    active_alerts = alert_svc.list_alerts(db, status=AlertStatus.GENERATED)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "page": "dashboard",
            "properties": properties,
            "health_scores": health_scores,
            "overdue_tasks": overdue,
            "upcoming_tasks": upcoming[:10],
            "alerts": active_alerts[:5],
        },
    )
