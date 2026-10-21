"""Alerts routes."""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.alert import Alert, AlertStatus
from mihomes.services import alerts as alert_svc
from mihomes.web.deps import get_db, templates

router = APIRouter()


def _list_response(request: Request, db: Session, message: str):
    """Re-render the list after an action, with a toast and an `alerts-changed` event.

    The page header's count and the sidebar badge sit outside `#alert-list`, so after resolving
    the last alert the list said "All clear" while the header still said "1 active alert". The
    partial refreshes the header count out-of-band; the event makes the badge re-poll now
    instead of within 60 seconds.
    """
    alerts = alert_svc.list_alerts(db, include_snoozed=True)
    response = templates.TemplateResponse(
        request,
        "partials/alert_list.html",
        {"alerts": alerts, "oob_count": True},
    )
    response.headers["HX-Trigger"] = json.dumps(
        {"toast": {"message": message, "kind": "success"}, "alerts-changed": True}
    )
    return response


@router.get("/")
@declares("issue.manage", Access.COLLECTION)
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
@declares("issue.manage", Access.COLLECTION)
def alert_badge(request: Request, db: Session = Depends(get_db)):
    alerts = alert_svc.list_alerts(db)
    return templates.TemplateResponse(
        request,
        "partials/alert_badge.html",
        {"count": len(alerts)},
    )


@router.post("/{alert_id}/acknowledge", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def acknowledge(request: Request, alert_id: UUID, db: Session = Depends(get_db)):
    alert_svc.acknowledge_alert(db, alert_id)
    return _list_response(request, db, "Alert acknowledged")


@router.post("/{alert_id}/snooze", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def snooze(
    request: Request,
    alert_id: UUID,
    days: int = Form(1),
    db: Session = Depends(get_db),
):
    alert_svc.snooze_alert(db, alert_id, days=days)
    return _list_response(request, db, f"Snoozed for {days} day{'s' if days != 1 else ''}")


@router.post("/{alert_id}/resolve", response_class=HTMLResponse)
@declares("issue.manage", Access.ITEM)
def resolve(request: Request, alert_id: UUID, db: Session = Depends(get_db)):
    alert = db.get(Alert, alert_id)
    if alert:
        alert.status = AlertStatus.RESOLVED
        db.flush()
    return _list_response(request, db, "Alert resolved")
