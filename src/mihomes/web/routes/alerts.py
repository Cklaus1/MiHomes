"""Alerts routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.models.alert import Alert, AlertStatus
from mihomes.services import alerts as alert_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


@router.get("/")
def list_alerts(request: Request, db: Session = Depends(get_db)):
    alerts = alert_svc.list_alerts(db, include_snoozed=True)
    critical = [a for a in alerts if a.severity.value == "critical"]
    high = [a for a in alerts if a.severity.value == "high"]
    medium = [a for a in alerts if a.severity.value == "medium"]
    low = [a for a in alerts if a.severity.value == "low"]
    return templates.TemplateResponse(
        request,
        "alerts.html",
        {
            "page": "alerts",
            "alerts": alerts,
            "critical": critical,
            "high": high,
            "medium": medium,
            "low": low,
        },
    )


@router.get("/badge", response_class=HTMLResponse)
def alert_badge(request: Request, db: Session = Depends(get_db)):
    alerts = alert_svc.list_alerts(db)
    return templates.TemplateResponse(
        request,
        "partials/alert_badge.html",
        {"count": len(alerts)},
    )


@router.post("/{alert_id}/acknowledge", response_class=HTMLResponse)
def acknowledge(request: Request, alert_id: UUID, db: Session = Depends(get_db)):
    alert_svc.acknowledge_alert(db, alert_id)
    alerts = alert_svc.list_alerts(db, include_snoozed=True)
    return templates.TemplateResponse(
        request,
        "partials/alert_list.html",
        {"alerts": alerts},
    )


@router.post("/{alert_id}/snooze", response_class=HTMLResponse)
def snooze(
    request: Request,
    alert_id: UUID,
    days: int = Form(1),
    db: Session = Depends(get_db),
):
    alert_svc.snooze_alert(db, alert_id, days=days)
    alerts = alert_svc.list_alerts(db, include_snoozed=True)
    return templates.TemplateResponse(
        request,
        "partials/alert_list.html",
        {"alerts": alerts},
    )


@router.post("/{alert_id}/resolve", response_class=HTMLResponse)
def resolve(request: Request, alert_id: UUID, db: Session = Depends(get_db)):
    alert = db.get(Alert, alert_id)
    if alert:
        alert.status = AlertStatus.RESOLVED
        db.flush()
    alerts = alert_svc.list_alerts(db, include_snoozed=True)
    return templates.TemplateResponse(
        request,
        "partials/alert_list.html",
        {"alerts": alerts},
    )
