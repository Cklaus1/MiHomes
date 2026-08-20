"""AI Advisor route."""

import json
import logging
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.document import DocumentType
from mihomes.services import document as doc_svc
from mihomes.services import property as prop_svc
from mihomes.services.ai.file_processor import Attachment, process_upload
from mihomes.web.deps import get_db, templates
from mihomes.web.forms import save_document_text

logger = logging.getLogger(__name__)

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

ROLE_DISPLAY = {
    "estate_manager": "Manager",
    "maintenance": "Maintenance",
    "financial": "Financial",
    "vendor_strategist": "Vendor",
    "compliance_officer": "Compliance",
    "hospitality": "Hospitality",
    "housekeeping": "Housekeeping",
    "grounds": "Grounds",
    "security": "Security",
}

# SPEC-003 §3/Step 15 — these told a *browser* user to "run `mihomes ai setup` in the CLI",
# which SPEC-002 D1 had already made impossible: the CLI is an operator tool and there is no
# user-facing one. The advice was not merely unhelpful, it was unfollowable. Now that Step 15's
# settings page exists, point at it.
#
# G6.4 deferred this deliberately rather than deleting it early: removing the wrong advice before
# the right destination existed would have left a worse message than the wrong one.
_AI_ERROR_HINT = (
    "AI provider not configured. An owner or admin can set the API key in Settings."
)
_AI_INVALID_KEY_HINT = (
    "API key is invalid or rejected. An owner or admin can update it in Settings."
)


def _session_property_slug(context_summary: str | None) -> str:
    """Parse context_summary like 'Agent; roles: financial; property: belle-estate' to extract the property slug."""
    if not context_summary:
        return ""
    for part in context_summary.split(";"):
        part = part.strip()
        if part.startswith("property:"):
            value = part[len("property:"):].strip()
            if value and value != "all":
                return value
    return ""


def _list_sessions(db: Session) -> list[dict]:
    """Return the most recent 50 sessions, one entry per session_id.

    **Grouped on `created_at`, not on `id`, and that is not a substitution.** This function used to
    take `func.min(AIConversation.id)` to find each session's first message — which worked only
    because `id` was a sequential integer, making "smallest id in the group" accidentally mean
    "earliest row". SPEC-002 G6.1 converted every primary key to UUIDv7 and Postgres has no
    `min(uuid)`, so `/ai/` and `/ai/sessions-panel` returned 500 to **every** role, owners
    included, from that conversion until this fix.

    `created_at` is the column that carried the ordering intent the whole time. UUIDv7 is
    time-ordered as well, so a v7-aware aggregate could answer the same question — but relying on
    that would re-encode the same accident that broke this once already.

    The join is on `(session_id, created_at)` rather than on the id, because the id is precisely
    what the aggregate can no longer produce. A session with two messages sharing a `created_at`
    to the microsecond would yield two rows; `DISTINCT ON` would be the airtight form, but it is
    Postgres-specific syntax for a collision that requires two inserts in the same transaction at
    identical timestamps, and the duplicate would be cosmetic (one sidebar entry twice) rather
    than wrong.
    """
    from sqlalchemy import func

    from mihomes.models.ai_conversation import AIConversation

    # Per session_id: when it started (identifies the first message), when it was last touched
    # (sort key), and how many messages it holds.
    sub = (
        db.query(
            AIConversation.session_id.label("session_id"),
            func.min(AIConversation.created_at).label("first_at"),
            func.max(AIConversation.created_at).label("last_at"),
            func.count(AIConversation.id).label("msg_count"),
        )
        .group_by(AIConversation.session_id)
        .subquery()
    )

    rows = (
        db.query(AIConversation, sub.c.last_at, sub.c.msg_count)
        .join(
            sub,
            (AIConversation.session_id == sub.c.session_id)
            & (AIConversation.created_at == sub.c.first_at),
        )
        .order_by(sub.c.last_at.desc())
        .limit(50)
        .all()
    )

    result = []
    for conv, last_at, msg_count in rows:
        custom_name = (conv.session_name or "").strip()
        result.append({
            "session_id": conv.session_id,
            "title": custom_name or (conv.user_message or "")[:55],
            "custom_name": custom_name,
            "role": conv.role or "",
            "role_label": ROLE_DISPLAY.get(conv.role or "", "Auto"),
            "property": _session_property_slug(conv.context_summary),
            "last_at": last_at,
            "msg_count": msg_count,
        })
    return result


def _group_sessions(sessions: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group sessions into Today / Yesterday / Last 7 days / Older, omitting empty groups."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)

    groups: dict[str, list[dict]] = {
        "Today": [],
        "Yesterday": [],
        "Last 7 days": [],
        "Older": [],
    }

    for s in sessions:
        last_at = s["last_at"]
        if isinstance(last_at, datetime):
            d = last_at.date()
        else:
            d = last_at
        if d == today:
            groups["Today"].append(s)
        elif d == yesterday:
            groups["Yesterday"].append(s)
        elif d > week_ago:
            groups["Last 7 days"].append(s)
        else:
            groups["Older"].append(s)

    return [(label, items) for label, items in groups.items() if items]


# M20: cap AI uploads so a caller can't attach hundreds of files or a huge
# aggregate payload. Mirrors read_image_uploads' per-request count/size caps;
# per-file image/text truncation still happens downstream in process_upload.
_MAX_ATTACH_FILES = 6
_MAX_ATTACH_TOTAL_BYTES = 20 * 1024 * 1024  # 20 MiB across all attachments


async def _read_attachments(files: list[UploadFile]) -> list[Attachment]:
    real = [f for f in files if f.filename]
    if len(real) > _MAX_ATTACH_FILES:
        raise ValueError(
            f"Please attach at most {_MAX_ATTACH_FILES} files (got {len(real)})."
        )
    result = []
    total = 0
    for f in real:
        data = await f.read()
        if not data:
            continue
        total += len(data)
        if total > _MAX_ATTACH_TOTAL_BYTES:
            raise ValueError(
                f"Total attachment size exceeds {_MAX_ATTACH_TOTAL_BYTES // (1024 * 1024)} MB."
            )
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
@declares("ai.use", Access.COLLECTION)
def ai_page(request: Request, db: Session = Depends(get_db)):
    from mihomes.models.work_order import WorkOrder, WorkOrderStatus

    work_orders = (
        db.query(WorkOrder)
        .filter(WorkOrder.status.notin_([WorkOrderStatus.CANCELLED]))
        .order_by(WorkOrder.created_at.desc())
        .limit(60)
        .all()
    )

    sessions = _list_sessions(db)
    session_groups = _group_sessions(sessions)

    return templates.TemplateResponse(request, "ai.html", {
        "page": "ai",
        "properties": prop_svc.list_properties(db),
        "roles": ROLES,
        "work_orders": work_orders,
        "session_groups": session_groups,
    })


# ---------------------------------------------------------------------------------------
# The transcript store — NOT the assistant. SPEC-003 G17.
#
# The four routes below read, rename and delete *saved conversations*, and they used to declare
# `ai.use` like the rest of this module. Row 18 grants `ai.use` to staff as SCOPED, so a scoped
# housekeeper could GET /ai/sessions/{id} and read an owner's answer verbatim — including the
# financial ones G10 spent a whole group keeping out of the live path. G10 scoped the *question*;
# the **transcript of an already-answered question** is a stored row, and no scope reaches it:
# `AIConversation` is ACCOUNT_LEVEL, which has no query-layer enforcement, and it carries no
# author column at all (`role` is the AI persona — "financial", "estate_manager" — not the member
# who asked). So there is nothing to scope *by* even in principle.
#
# `audit.view` (row 17) is the honest key available today: account-level, denied to staff, and
# "read the account's historical record" is what these routes do. It is **approximate** — the
# right answer is a `transcript.view` key, or an author column so a member can read their own
# history. Recorded in `opportunities.md`; same shape as G6's four other approximate declarations.
#
# `/ai/` and `/ai/ask` deliberately keep `ai.use`: staff *may* use the assistant, and denying
# that to fix this would break a capability the matrix grants — the over-correction /library/
# avoided.
# ---------------------------------------------------------------------------------------


@router.get("/sessions-panel", response_class=HTMLResponse)
@declares("audit.view", Access.ACCOUNT)
def ai_sessions_panel(request: Request, db: Session = Depends(get_db)):
    sessions = _list_sessions(db)
    session_groups = _group_sessions(sessions)
    return templates.TemplateResponse(request, "partials/ai_sessions_panel.html", {
        "session_groups": session_groups,
    })


@router.get("/sessions/{session_id}", response_class=JSONResponse)
@declares("audit.view", Access.ACCOUNT)
def ai_session_messages(session_id: str, db: Session = Depends(get_db)):
    from mihomes.models.ai_conversation import AIConversation

    rows = (
        db.query(AIConversation)
        .filter(AIConversation.session_id == session_id)
        .order_by(AIConversation.created_at.asc())
        .all()
    )
    return [
        {"user": r.user_message, "ai": r.ai_response, "role": r.role or ""}
        for r in rows
    ]


@router.delete("/sessions/{session_id}", response_class=JSONResponse)
@declares("audit.view", Access.ACCOUNT)
def ai_delete_session(session_id: str, db: Session = Depends(get_db)):
    from mihomes.models.ai_conversation import AIConversation

    db.query(AIConversation).filter(AIConversation.session_id == session_id).delete()
    db.flush()
    return {"ok": True}


@router.patch("/sessions/{session_id}/name", response_class=JSONResponse)
@declares("audit.view", Access.ACCOUNT)
async def ai_rename_session(session_id: str, request: Request, db: Session = Depends(get_db)):
    from mihomes.models.ai_conversation import AIConversation

    body = await request.json()
    name = (body.get("name") or "").strip()[:120]
    first_row = (
        db.query(AIConversation)
        .filter(AIConversation.session_id == session_id)
        .order_by(AIConversation.id.asc())
        .first()
    )
    if first_row:
        first_row.session_name = name or None
        db.flush()
    return {"ok": True, "name": name}


@router.post("/ask", response_class=HTMLResponse)
@declares("ai.use", Access.COLLECTION)
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
@declares("ai.use", Access.COLLECTION)
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
@declares("ai.use", Access.COLLECTION)
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
@declares("ai.use", Access.COLLECTION)
async def ai_ask_stream(
    request: Request,
    query: str = Form(...),
    role: str = Form(""),
    property_id: str = Form(""),
    session_id: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    from mihomes.models.ai_conversation import AIConversation
    from mihomes.services.ai.agent import agent_stream, provider_stream
    from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_model, get_ai_provider_name
    from mihomes.services.ai.roles import route_query

    session_id = session_id or str(uuid.uuid4())

    try:
        attachments = await _read_attachments(files)
        provider_name = get_ai_provider_name(db)
        api_key = get_ai_api_key(db, provider_name)
        model = get_ai_model(db, provider_name)
        roles = route_query(query, explicit_role=role or None)
        primary_role = roles[0]
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
    import contextvars

    async def _generate():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        # SPEC-002: the tenant is carried in a ContextVar, and `run_in_executor` does
        # NOT propagate context to the worker (unlike `asyncio.to_thread`, which copies
        # it). Without this the worker's `current_account` is unset, the G8.3 insert
        # listener fails closed with LookupError, and the conversation save below is
        # swallowed by the `except Exception` — the stream would still look fine to the
        # user while silently persisting nothing. Capture the request's context here and
        # run the worker inside a copy of it.
        ctx = contextvars.copy_context()

        def _run_sync():
            # H14: the streaming work runs in a worker thread. It opens its own
            # dedicated DB session (never touching the request-scoped `db` across
            # threads) both for the agent's own reads and to persist the finished
            # conversation from inside the worker, committing it. A failed save is
            # logged, not silently swallowed.
            from mihomes.db import get_session as _get_worker_session

            worker_parts: list[str] = []
            try:
                with _get_worker_session() as worker_db:
                    if provider_name == "claude":
                        stream_gen = agent_stream(
                            worker_db, query,
                            system_prompt=system_prompt,
                            api_key=api_key,
                            model=model,
                            property_slug=property_id or None,
                            attachments=attachments or None,
                        )
                    else:
                        stream_gen = provider_stream(
                            worker_db, query,
                            system_prompt=system_prompt,
                            provider_name=provider_name,
                            api_key=api_key,
                            model=model,
                            roles=roles,
                            property_slug=property_id or None,
                            attachments=attachments or None,
                        )
                    for event_type, data in stream_gen:
                        if event_type == "token":
                            worker_parts.append(data)
                        loop.call_soon_threadsafe(queue.put_nowait, (event_type, data))

                    # Persist the finished conversation from the worker session.
                    worker_db.add(AIConversation(
                        session_id=session_id,
                        role=primary_role.name,
                        user_message=query,
                        ai_response="".join(worker_parts),
                        context_summary=f"Agent; roles: {', '.join(r.name for r in roles)}; property: {property_id or 'all'}",
                        provider=provider_name,
                        model=model,
                    ))
            except Exception as exc:
                logger.exception("ai_ask_stream worker failed")
                loop.call_soon_threadsafe(queue.put_nowait, ("error", _ai_error(str(exc))))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, (None, None))

        future = loop.run_in_executor(None, ctx.run, _run_sync)

        while True:
            msg_type, msg_data = await queue.get()
            if msg_type is None:
                break
            if msg_type == "token":
                yield f"data: {json.dumps({'t': msg_data})}\n\n"
            elif msg_type == "status":
                yield f"data: {json.dumps({'status': msg_data})}\n\n"
            elif msg_type == "error":
                yield f"data: {json.dumps({'e': _ai_error(msg_data)})}\n\n"

        yield "data: [DONE]\n\n"
        await future

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/save-report", response_class=HTMLResponse)
@declares("ai.use", Access.COLLECTION)
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
    header = f"# {subject}\n\n**Type:** {report_type}  \n**Generated:** {generated_at}\n\n---\n\n"
    file_path = save_document_text(f"report-{slug_part}", header + report_text)

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
