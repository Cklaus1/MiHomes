"""AI Advisor route."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.services import property as prop_svc
from mihomes.services.ai.file_processor import Attachment, process_upload
from mihomes.web.deps import get_db, templates

router = APIRouter()

ROLES = [
    ("", "Auto-route"),
    ("estate_manager", "Estate Manager"),
    ("maintenance", "Maintenance Advisor"),
    ("financial", "Financial Analyst"),
    ("vendor_strategist", "Vendor Strategist"),
    ("compliance_officer", "Compliance Monitor"),
    ("hospitality", "Hospitality Planner"),
    ("housekeeping", "Housekeeping Supervisor"),
    ("grounds", "Grounds Manager"),
    ("security", "Security Advisor"),
]

_AI_ERROR_HINT = "AI provider not configured. Run `mihomes ai setup` in the CLI to set your API key."
_AI_INVALID_KEY_HINT = "API key is invalid or rejected. Run `mihomes ai setup` in the CLI to update your API key."


async def _read_attachments(files: list[UploadFile]) -> list[Attachment]:
    result = []
    for f in files:
        if not f.filename:
            continue
        data = await f.read()
        if not data:
            continue
        att = process_upload(f.filename, data, f.content_type or "")
        if att:
            result.append(att)
    return result


def _ai_error(msg: str) -> str:
    lower = msg.lower()
    if any(k in lower for k in ("not found", "not configured", "no provider", "run: mihomes")):
        return _AI_ERROR_HINT
    if any(k in lower for k in ("invalid api key", "authentication", "unauthorized", "401")):
        return _AI_INVALID_KEY_HINT
    return f"AI request failed: {msg}"


@router.get("/")
def ai_page(request: Request, db: Session = Depends(get_db)):
    from mihomes.models.work_order import WorkOrder, WorkOrderStatus
    work_orders = (
        db.query(WorkOrder)
        .filter(WorkOrder.status.notin_([WorkOrderStatus.CANCELLED]))
        .order_by(WorkOrder.created_at.desc())
        .limit(60)
        .all()
    )
    return templates.TemplateResponse(request, "ai.html", {
        "page": "ai",
        "properties": prop_svc.list_properties(db),
        "roles": ROLES,
        "work_orders": work_orders,
    })


@router.post("/ask", response_class=HTMLResponse)
async def ai_ask(
    request: Request,
    query: str = Form(...),
    role: str = Form(""),
    property_id: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    from mihomes.services.ai.orchestrator import ask

    error = None
    response_text = ""
    active_role = ""

    try:
        attachments = await _read_attachments(files)
        resp = ask(db, query, role=role or None, property_slug=property_id or None, attachments=attachments or None)
        response_text = resp.text
        active_role = resp.role
    except Exception as e:
        error = _ai_error(str(e))

    return templates.TemplateResponse(request, "partials/ai_message.html", {
        "query": query,
        "response_text": response_text,
        "active_role": active_role,
        "error": error,
    })


@router.post("/situation-report", response_class=HTMLResponse)
async def situation_report(
    request: Request,
    subject: str = Form(""),
    content: str = Form(...),
    work_order_slug: str | None = Form(None),
    property_slug: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    from mihomes.services.ai.reports import generate_situation_report

    error = None
    report_text = ""
    try:
        attachments = await _read_attachments(files)
        resp = generate_situation_report(
            db, content,
            subject=subject,
            work_order_slug=work_order_slug or None,
            property_slug=property_slug or None,
            attachments=attachments or None,
        )
        report_text = resp.text
    except Exception as e:
        error = _ai_error(str(e))

    return templates.TemplateResponse(request, "partials/report_output.html", {
        "report_type": "Situation Report",
        "subject": subject or "Advisory Report",
        "report_text": report_text,
        "generated_at": datetime.now().strftime("%B %d, %Y at %I:%M %p"),
        "error": error,
    })


@router.post("/estate-digest", response_class=HTMLResponse)
async def estate_digest(
    request: Request,
    period: str = Form("this_week"),
    property_slug: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    from mihomes.services.ai.reports import generate_estate_digest

    today = date.today()

    period_map = {
        "this_week":  (today - timedelta(days=today.weekday()), today, "This Week"),
        "last_7":     (today - timedelta(days=7), today, "Last 7 Days"),
        "last_week":  (today - timedelta(days=today.weekday() + 7),
                       today - timedelta(days=today.weekday() + 1), "Last Week"),
        "this_month": (today.replace(day=1), today, "This Month"),
        "last_30":    (today - timedelta(days=30), today, "Last 30 Days"),
        "last_month": (
            (lambda d: d.replace(day=1))(today.replace(day=1) - timedelta(days=1)),
            today.replace(day=1) - timedelta(days=1),
            "Last Month",
        ),
    }
    start, end, period_label = period_map.get(period, (today - timedelta(days=7), today, "Last 7 Days"))

    error = None
    report_text = ""
    try:
        attachments = await _read_attachments(files)
        resp = generate_estate_digest(
            db, start, end,
            property_slug=property_slug or None,
            attachments=attachments or None,
        )
        report_text = resp.text
    except Exception as e:
        error = _ai_error(str(e))

    return templates.TemplateResponse(request, "partials/report_output.html", {
        "report_type": "Estate Digest",
        "subject": f"{period_label} — {start.strftime('%b %d')} to {end.strftime('%b %d, %Y')}",
        "report_text": report_text,
        "generated_at": datetime.now().strftime("%B %d, %Y at %I:%M %p"),
        "error": error,
    })
