"""WhatsApp conversation review — AI-powered passive issue detection."""

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
                        "enum": ["issue", "task", "task_completion", "supply_need", "vendor_activity", "question", "pto_request", "informational"],
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "reported_by": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "assigned_to": {"type": "string"},
                    "pto_dates": {"type": "array", "items": {"type": "string"}},
                    "related_asset": {"type": "string"},
                    "quantity_in_stock": {"type": "number"},
                    "quantity_to_order": {"type": "number"},
                    "unit": {"type": "string"},
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

    # Open issues
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

    # Recently resolved issues (last 30 days)
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

    # Active assets
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

    # Active staff assigned to this property
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
    """Analyze WhatsApp messages and extract actionable items.

    Returns dict with 'items' (actionable) and 'skipped' (non-actionable).
    """
    if not messages:
        return {"items": [], "skipped": []}

    # Format messages for AI — skip entirely empty (no text, no media)
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

        # Load image from disk if available
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
        "You are analyzing a WhatsApp staff group chat for a property management system. "
        "Extract ALL actionable items from the conversation. "
        "Photos attached to messages are real images from the property — analyze their visual content "
        "when classifying and describing items (e.g. damage visible in a photo should inform severity).\n\n"
        "Classify each message or message cluster into:\n"
        "- issue: reporting something broken, damaged, malfunctioning, or needing repair (e.g. 'toilet is broken', 'AC not working')\n"
        "- task: requesting a specific action be performed (e.g. 'please clean the pool', 'order more towels')\n"
        "- task_completion: confirming work was completed (e.g. 'done', 'finished the cleaning')\n"
        "- supply_need: something needs purchasing or restocking. "
        "Extract quantity_in_stock if the message says how much is left (e.g. '1 bottle left', 'only 2 rolls'). "
        "Extract quantity_to_order if the message says how much to buy (e.g. 'need to order 3', 'get 2 more'). "
        "Extract unit if mentioned (bottles, rolls, bags, boxes, etc.).\n"
        "- vendor_activity: a vendor visit or service happening\n"
        "- question: asking for information, status, schedules, or updates (e.g. 'what is the AC status?', 'when is the next pool check?', 'has the plumber been called?', 'check AC repair status')\n"
        "- pto_request: a staff member requesting time off (e.g. 'can I have Friday off', 'requesting PTO Dec 24-26', 'I need next Monday off'). Extract dates into pto_dates as YYYY-MM-DD strings.\n"
        "- informational: social chat, greetings, personal messages, or anything unrelated to the home\n\n"
        "IMPORTANT DISTINCTION — question vs task:\n"
        "- If the message is asking for INFORMATION or STATUS → question\n"
        "- If the message is reporting a PROBLEM → issue\n"
        "- If the message is requesting an ACTION to be performed → task\n"
        "'Check X status', 'what is the status of X', 'has X been done', 'when is X scheduled' are ALL questions.\n\n"
        "For questions: 'title' = concise restatement, 'description' = full question text verbatim.\n"
        "For issues/tasks: extract title, description, severity (if issue), reporter, assigned_to (name of person the task is assigned to if mentioned), related asset.\n\n"
        "Only classify as 'question' if genuinely about the home, property, maintenance, staff, vendors, or estate. "
        "Greetings and off-topic chat are 'informational'.\n\n"
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
