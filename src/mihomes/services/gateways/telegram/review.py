"""Telegram conversation review — AI-powered passive issue detection."""

import base64
import mimetypes
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mihomes.models.asset import Asset
from mihomes.models.issue import Issue, IssueStatus
from mihomes.models.property import Property
from mihomes.models.staff import Staff
from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_model, get_ai_provider_name
from mihomes.services.ai.file_processor import Attachment
from mihomes.services.ai.provider import get_provider
from mihomes.services.slug import resolve_identifier

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "issue", "task", "task_completion", "supply_need",
                            "vendor_activity", "question", "pto_request",
                            "book_addition", "asset_addition", "issue_resolution",
                            "work_order_request", "appointment_request",
                            "expense_log", "note_addition", "informational",
                        ],
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "reported_by": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "assigned_to": {"type": "string"},
                    "pto_dates": {"type": "array", "items": {"type": "string"}},
                    "room": {"type": "string"},
                    "related_asset": {"type": "string"},
                    "quantity_in_stock": {"type": "number"},
                    "quantity_to_order": {"type": "number"},
                    "unit": {"type": "string"},
                    # book_addition
                    "books": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "author": {"type": "string"},
                                "genre": {"type": "string"},
                                "isbn": {"type": "string"},
                                "condition": {
                                    "type": "string",
                                    "enum": ["excellent", "good", "fair", "poor", "damaged"],
                                },
                            },
                            "required": ["title"],
                        },
                    },
                    # asset_addition
                    "assets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "asset_type": {
                                    "type": "string",
                                    "enum": ["appliance", "vehicle", "valuable", "equipment", "consumable"],
                                },
                                "condition": {
                                    "type": "string",
                                    "enum": ["excellent", "good", "fair", "poor"],
                                },
                                "estimated_value": {"type": "number"},
                                "make": {"type": "string"},
                                "model": {"type": "string"},
                            },
                            "required": ["name"],
                        },
                    },
                    # shared across several new categories
                    "vendor_name": {"type": "string"},
                    "amount": {"type": "number"},
                    "expense_category": {"type": "string"},
                    "date": {"type": "string"},
                    "entity_type": {"type": "string"},
                    "entity_ref": {"type": "string"},
                    "note_text": {"type": "string"},
                    "resolution_notes": {"type": "string"},
                    "issue_ref": {"type": "string"},
                    "task_ref": {"type": "string"},
                    "appointment_type": {"type": "string"},
                },
                "required": ["category", "title"],
            },
        },
        "skipped": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        },
    },
    "required": ["items"],
}


def _build_estate_context(session: Session, property_slug: str | None) -> str:
    """Build an estate context block to inject into the AI prompt."""
    if not property_slug:
        return ""

    try:
        prop = resolve_identifier(session, Property, property_slug)
    except Exception:
        return ""

    lines = [f"## Estate Context — {prop.name}"]

    open_statuses = [
        IssueStatus.REPORTED,
        IssueStatus.ASSESSED,
        IssueStatus.SCHEDULED,
        IssueStatus.IN_PROGRESS,
    ]
    open_issues = (
        session.query(Issue)
        .filter(Issue.property_id == prop.id, Issue.status.in_(open_statuses))
        .order_by(Issue.created_at.desc())
        .limit(20)
        .all()
    )
    if open_issues:
        lines.append("\n### Open Issues")
        for issue in open_issues:
            age_days = (datetime.now(timezone.utc).replace(tzinfo=None) - issue.created_at).days
            lines.append(
                f"- [{issue.severity.value}] {issue.title} — open {age_days}d (slug: {issue.slug})"
            )
    else:
        lines.append("\n### Open Issues\n- None")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    resolved_issues = (
        session.query(Issue)
        .filter(
            Issue.property_id == prop.id,
            Issue.status.in_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
            Issue.resolved_at >= cutoff,
        )
        .order_by(Issue.resolved_at.desc())
        .limit(10)
        .all()
    )
    if resolved_issues:
        lines.append("\n### Recently Resolved Issues (last 30 days)")
        for issue in resolved_issues:
            resolved_str = issue.resolved_at.strftime("%Y-%m-%d") if issue.resolved_at else "unknown"
            lines.append(f"- {issue.title} — resolved {resolved_str}")

    assets = (
        session.query(Asset)
        .filter(Asset.property_id == prop.id, Asset.active.is_(True))
        .order_by(Asset.name)
        .all()
    )
    if assets:
        lines.append("\n### Tracked Assets")
        for asset in assets:
            descriptor = asset.make or asset.asset_type.value
            lines.append(f"- {asset.name} ({descriptor}) [slug: {asset.slug}]")

    staff_members = (
        session.query(Staff)
        .filter(Staff.active.is_(True), Staff.properties.any(Property.id == prop.id))
        .order_by(Staff.name)
        .all()
    )
    if staff_members:
        lines.append("\n### Staff")
        for member in staff_members:
            lines.append(f"- {member.name} ({member.role.value}) [slug: {member.slug}]")

    return "\n".join(lines)


def analyze_messages(
    session: Session,
    messages: list[dict],
    property_name: str | None = None,
    property_slug: str | None = None,
) -> dict:
    """Analyze Telegram messages and extract actionable items.

    Returns dict with 'items' (actionable) and 'skipped' (non-actionable).
    Message dicts use the same internal format as the WhatsApp pipeline.
    """
    if not messages:
        return {"items": [], "skipped": []}

    formatted = []
    image_attachments: list[Attachment] = []
    for msg in messages:
        sender = msg.get("senderName", "Unknown")
        text = msg.get("text", "").strip()
        ts = msg.get("timestamp", "")
        has_media = msg.get("hasMedia", False)
        media_path = msg.get("mediaPath")
        if not text and not has_media:
            continue

        if has_media and media_path and os.path.isfile(media_path):
            try:
                mime = mimetypes.guess_type(media_path)[0] or "image/jpeg"
                if mime.startswith("image/"):
                    with open(media_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    image_attachments.append(Attachment(
                        filename=os.path.basename(media_path),
                        is_image=True,
                        base64_data=b64,
                        media_type=mime,
                    ))
                    media_label = f" [image: {os.path.basename(media_path)}]"
                else:
                    media_label = " [file attached]"
            except OSError:
                media_label = " [photo attached]"
        elif has_media:
            media_label = " [photo attached]"
        else:
            media_label = ""

        if not text and has_media:
            text = "(sent a photo)"
        formatted.append(f"[{ts}] {sender}: {text}{media_label}")

    conversation_text = "\n".join(formatted)

    system_prompt = (
        "You are analyzing a Telegram staff group chat for a property management system. "
        "Extract ALL actionable items from the conversation. "
        "Photos attached to messages are real images from the property — analyze their visual content "
        "when classifying and describing items.\n\n"
        "Classify each message or message cluster into one of these categories:\n\n"
        "MAINTENANCE & OPERATIONS:\n"
        "- issue: reporting something broken, damaged, malfunctioning, or needing repair (e.g. 'toilet is broken', 'AC not working'). "
        "Damage visible in photos should inform severity.\n"
        "- issue_resolution: staff confirming something is now fixed or resolved (e.g. 'the AC is working now', 'plumber finished', 'toilet fixed'). "
        "Extract issue_ref (the name/description of the issue resolved) and resolution_notes if provided.\n"
        "- task: requesting a specific action be performed (e.g. 'please clean the pool', 'order more towels')\n"
        "- task_completion: confirming a task was completed (e.g. 'done', 'finished the pool cleaning'). "
        "Extract task_ref (the task name) if mentioned.\n"
        "- work_order_request: requesting creation of a formal work order for a vendor job (e.g. 'create a work order for the roof leak', 'we need a work order for the HVAC'). "
        "Extract vendor_name and amount (estimated cost) if mentioned.\n"
        "- vendor_activity: a vendor visit or service that is happening or has happened\n"
        "- supply_need: something needs purchasing or restocking. "
        "Extract quantity_in_stock, quantity_to_order, and unit if mentioned.\n\n"
        "SCHEDULING & FINANCE:\n"
        "- appointment_request: scheduling a vendor visit, inspection, or service (e.g. 'schedule Orkin for Thursday', 'pest control coming Friday at 2pm'). "
        "Extract vendor_name, date (YYYY-MM-DD), and appointment_type (vendor_visit, inspection, delivery, maintenance, other).\n"
        "- expense_log: logging a cost, invoice, or payment (e.g. 'Orkin invoice was $450', 'paid $200 for pool chemicals'). "
        "Extract amount, vendor_name, expense_category (e.g. maintenance, landscaping, utilities, operations), and date.\n\n"
        "LIBRARY & ASSETS:\n"
        "- book_addition: adding books to the property library (e.g. 'add these to the library', 'these books are in the study'). "
        "When photos are attached, READ the book covers and spines directly from the images to extract title, author, and genre for each book. "
        "Populate the 'books' array with one entry per book identified. Also extract room if mentioned.\n"
        "- asset_addition: adding physical items to the asset inventory (e.g. 'add this to assets', 'log the new pressure washer', 'track this equipment'). "
        "When photos are attached, identify each trackable asset visible (furniture, electronics, appliances, equipment, valuables). "
        "Populate the 'assets' array. Also extract room if mentioned.\n\n"
        "NOTES & COMMUNICATION:\n"
        "- note_addition: attaching a note to an existing record (e.g. 'add note to the pool pump issue: part ordered', 'note on the Orkin contract: renewed'). "
        "Extract entity_type (issue, task, asset, vendor, workorder, contract), entity_ref (name/slug of the record), and note_text.\n"
        "- question: asking for information, status, schedules, or updates (e.g. 'what is the AC status?', 'when is the next pool check?'). "
        "Questions like 'are you working?', 'is the bot active?' are ALWAYS questions.\n"
        "- pto_request: a staff member requesting time off. Extract pto_dates as YYYY-MM-DD strings.\n"
        "- informational: social chat, greetings, personal messages, or anything genuinely unrelated to the property\n\n"
        "IMPORTANT DISTINCTIONS:\n"
        "- issue vs issue_resolution: 'toilet broken' → issue. 'toilet is fixed' → issue_resolution.\n"
        "- task vs task_completion: 'please clean pool' → task. 'pool cleaning done' → task_completion.\n"
        "- appointment_request vs vendor_activity: 'schedule Orkin for Thursday' → appointment_request. 'Orkin was here today' → vendor_activity.\n"
        "- question vs task: asking for INFO → question. requesting ACTION → task.\n\n"
        "For questions: title = concise restatement, description = full question text verbatim.\n"
        "For issues/tasks: extract title, description, severity, reporter, assigned_to, related_asset, and room.\n\n"
        "Also list skipped items with brief reasons.\n"
        "Be thorough — even terse messages like 'deer treatment' are task requests.\n"
        "Correlate related messages (e.g., low tire + possible hole = severity upgrade)."
    )

    estate_context = _build_estate_context(session, property_slug)
    property_header = f"Property: {property_name}" if property_name else ""
    context_parts = [p for p in [property_header, estate_context] if p]
    context = "\n\n".join(context_parts) if context_parts else ""

    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    model = get_ai_model(session, provider_name)
    provider = get_provider(provider_name, api_key, model=model)

    result = provider.structured_output(
        system_prompt, conversation_text, REVIEW_SCHEMA,
        context_data=context or None,
        attachments=image_attachments or None,
    )

    return result
