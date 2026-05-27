"""AI Advisor route."""

import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from mihomes.models.document import DocumentType
from mihomes.services import document as doc_svc
from mihomes.services import property as prop_svc
from mihomes.services.ai.file_processor import Attachment, process_upload
from mihomes.web.deps import get_db, templates

UPLOADS_DIR = Path(__file__).parent.parent / "static" / "uploads"

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
        "property_slug": property_slug or "",
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
        "property_slug": property_slug or "",
        "error": error,
    })


@router.post("/ask-stream")
async def ai_ask_stream(
    request: Request,
    query: str = Form(...),
    role: str = Form(""),
    property_id: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_model, get_ai_provider_name
    from mihomes.services.ai.agent import agent_stream
    from mihomes.services.ai.orchestrator import _get_session_id, _save_session_id
    from mihomes.services.ai.roles import route_query
    from mihomes.models.ai_conversation import AIConversation

    attachments = await _read_attachments(files)

    try:
        provider_name = get_ai_provider_name(db)
        api_key = get_ai_api_key(db, provider_name)
        model = get_ai_model(db, provider_name)
        roles = route_query(query, explicit_role=role or None)
        primary_role = roles[0]
        session_id = _get_session_id(False)
        if len(roles) > 1:
            system_prompt = (
                f"You are acting as multiple advisors: {', '.join(r.display_name for r in roles)}.\n\n"
                + roles[0].system_prompt
            )
        else:
            system_prompt = primary_role.system_prompt
    except Exception as e:
        error_msg = _ai_error(str(e))

        def _err_gen():
            yield f"data: {json.dumps({'e': error_msg})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(_err_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    import asyncio

    async def _generate():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _run_sync():
            try:
                for event_type, data in agent_stream(
                    db, query,
                    system_prompt=system_prompt,
                    api_key=api_key,
                    model=model,
                    property_slug=property_id or None,
                    attachments=attachments or None,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, (event_type, data))
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", _ai_error(str(exc))))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, (None, None))

        future = loop.run_in_executor(None, _run_sync)

        full_text_parts: list[str] = []
        while True:
            msg_type, msg_data = await queue.get()
            if msg_type is None:
                break
            if msg_type == "token":
                full_text_parts.append(msg_data)
                yield f"data: {json.dumps({'t': msg_data})}\n\n"
            elif msg_type == "status":
                yield f"data: {json.dumps({'status': msg_data})}\n\n"
            elif msg_type == "error":
                yield f"data: {json.dumps({'e': _ai_error(msg_data)})}\n\n"

        yield "data: [DONE]\n\n"
        await future

        try:
            response_text = "".join(full_text_parts)
            db.add(AIConversation(
                session_id=session_id,
                role=primary_role.name,
                user_message=query,
                ai_response=response_text,
                context_summary=f"Agent; roles: {', '.join(r.name for r in roles)}; property: {property_id or 'all'}",
                provider=provider_name,
                model=model,
            ))
            db.flush()
            _save_session_id(session_id)
        except Exception:
            pass

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/save-report", response_class=HTMLResponse)
async def save_report(
    request: Request,
    report_type: str = Form(...),
    subject: str = Form(...),
    report_text: str = Form(...),
    generated_at: str = Form(""),
    property_slug: str = Form(""),
    db: Session = Depends(get_db),
):
    slug_part = subject.lower().replace(" ", "-").replace("/", "-")[:40]
    filename = f"report-{slug_part}-{uuid.uuid4().hex[:8]}.md"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    header = f"# {subject}\n\n**Type:** {report_type}  \n**Generated:** {generated_at}\n\n---\n\n"
    (UPLOADS_DIR / filename).write_text(header + report_text, encoding="utf-8")
    file_path = f"/static/uploads/{filename}"

    prop = prop_svc.get_property(db, property_slug) if property_slug else None
    doc_svc.create_document(
        db,
        title=f"{report_type} — {subject}",
        file_path=file_path,
        document_type=DocumentType.REPORT,
        entity_type="property" if prop else None,
        entity_id=prop.id if prop else None,
    )

    return HTMLResponse("""
        <div class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs text-emerald-700
                    border border-emerald-200 rounded-lg bg-emerald-50">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
          </svg>
          Saved to Documents
        </div>
    """)
