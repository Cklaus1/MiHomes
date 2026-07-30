"""Shared gateway review + response core (R2.1).

Both the Telegram and WhatsApp responders were near-verbatim copies of the same
~500-line dispatch loop. They drifted: WhatsApp's copy dropped 8 of the 14
actionable categories, resolved vendors by a column that doesn't exist, wrote
issue photos into a phantom directory, and skipped the session rollback on AI
failure. This module is the single canonical implementation both delegate to.

Gateway differences are captured by a small `GatewayAdapter` (how to send a
message — client method + any emoji prefix) and two injected callables
(`resolve_reporter`, `is_approver`). Everything else — the schema, the
estate-context builder, `analyze_messages`, and the full category dispatch — is
shared, so a fix lands once and both gateways get it.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from mihomes.models.asset import Asset
from mihomes.models.issue import Issue, IssueStatus
from mihomes.models.property import Property
from mihomes.models.staff import Staff
from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_model, get_ai_provider_name
from mihomes.services.ai.file_processor import Attachment
from mihomes.services.ai.provider import get_provider
from mihomes.services.query_helpers import escape_like
from mihomes.services.slug import resolve_identifier

logger = logging.getLogger("mihomes.gateways")


# --------------------------------------------------------------------------- #
# Gateway adapter — the only thing that differs about *sending* a message
# --------------------------------------------------------------------------- #
@dataclass
class GatewayAdapter:
    """How a gateway delivers a reply.

    `send(chat_id, text)` bakes in the client call (Telegram `send_message`,
    WhatsApp `send_group_message`) and any presentation quirk (WhatsApp prefixes
    every message with "🏠 "). Callers in this module always pass plain text.
    """

    label: str
    send: Callable[[str, str], None]


# --------------------------------------------------------------------------- #
# Review schema — the SUPERSET of every category either gateway can act on (M24)
# --------------------------------------------------------------------------- #
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

# Categories this module knows how to dispatch. Anything the model returns that
# is not here (e.g. "informational") is intentionally a no-op, and anything a
# gateway *expects* but is missing here is reported by dispatch_items (L14).
DISPATCHABLE_CATEGORIES = {
    "issue", "task", "task_completion", "supply_need", "vendor_activity",
    "question", "pto_request", "book_addition", "asset_addition",
    "issue_resolution", "work_order_request", "appointment_request",
    "expense_log", "note_addition",
}

SYSTEM_PROMPT = (
    "You are analyzing a staff group chat for a property management system. "
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


# --------------------------------------------------------------------------- #
# Small shared helpers — each folds in a defect that had diverged per-gateway
# --------------------------------------------------------------------------- #
def uploads_dir() -> Path:
    """Return the real web uploads directory, anchored in the mihomes package.

    H26: the two responders computed this with different `__file__.parents[N]`
    depths. WhatsApp's `parents[4]` pointed at a `src/web/static/uploads` that
    does not exist, so issue photos were silently written into the void. This
    file lives at `src/mihomes/services/gateways/review_common.py`, so
    `parents[2]` is `src/mihomes/` for both gateways.
    """
    path = Path(__file__).resolve().parents[2] / "web" / "static" / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_event_date(timestamp_str: str | None) -> date | None:
    """Parse an AI-extracted timestamp/date string into a date, or None.

    L12: the old strptime table had no timezone-offset format, so any ISO-8601
    string with a `+00:00`/`-05:00` offset returned None and silently dropped
    the appointment/expense date. `fromisoformat` covers offsets natively; we
    normalize a trailing `Z` (which pre-3.11 fromisoformat rejects) first.
    """
    if not timestamp_str:
        return None
    candidate = timestamp_str.strip()
    if not candidate:
        return None
    iso = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
    try:
        return datetime.fromisoformat(iso).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def sender_phone(message: dict) -> str:
    """Normalized digits for the message sender.

    H25: the Baileys bridge sends `sender` as '15551234567@s.whatsapp.net', not
    `senderPhone`. The WhatsApp approval check read the missing key and never
    matched, so APPROVE/DENY replies were silently ignored. Derive from `sender`
    first, falling back to `senderPhone`.
    """
    raw = message.get("sender") or message.get("senderPhone") or ""
    return raw.split("@")[0].replace("+", "").replace("-", "").replace(" ", "")


def strip_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"#{1,6}\s*", "", text)
    text = re.sub(r"[-•]\s+", "", text)
    return text.strip()


def resolve_vendor(session: Session, name: str | None):
    """Find a vendor by fuzzy company-name match.

    H36/M38: `Vendor` has `company_name`, not `name`. The Telegram copy filtered
    on the nonexistent attribute (an AttributeError at query-build time) and had
    no LIKE escaping. Match on `company_name` with escaped wildcards.
    """
    if not name:
        return None
    from mihomes.models.vendor import Vendor
    return (
        session.query(Vendor)
        .filter(Vendor.company_name.ilike(f"%{escape_like(name)}%", escape="\\"))
        .first()
    )


def resolve_staff_slug(session: Session, name: str | None) -> str | None:
    if not name:
        return None
    staff = session.query(Staff).filter(Staff.name.ilike(f"%{escape_like(name)}%", escape="\\")).first()
    return staff.slug if staff else None


def resolve_reporter_by_name(session: Session, name: str | None) -> int | None:
    """Return staff.id for a reporter matched by fuzzy name, or None.

    Telegram's only reporter signal (it has no phone numbers). WhatsApp wraps
    this with an additional phone-match step in its own responder.
    """
    if not name:
        return None
    staff = session.query(Staff).filter(Staff.name.ilike(f"%{escape_like(name)}%", escape="\\")).first()
    return staff.id if staff else None


def resolve_default_property(session: Session) -> str | None:
    """Best-effort property slug when a message carries none (L2).

    Returns the sole property's slug if exactly one exists, else None. Never a
    hardcoded slug: on a multi-property estate "belle-estate" silently misfiled
    every unlabelled message; on a fresh install it pointed at a nonexistent
    property.
    """
    props = session.query(Property).limit(2).all()
    return props[0].slug if len(props) == 1 else None


def resolve_room(session: Session, room_name: str | None, prop_slug: str | None) -> str | None:
    if not room_name or not prop_slug:
        return None
    from mihomes.models.space import Space
    prop = session.query(Property).filter(Property.slug == prop_slug).first()
    if not prop:
        return None
    spaces = session.query(Space).filter(Space.property_id == prop.id).all()
    room_lower = room_name.lower()
    for space in spaces:
        if room_lower in space.name.lower() or space.name.lower() in room_lower:
            return space.slug
    return None


def ai_response(session: Session, prompt: str, role: str, property_slug: str | None) -> str | None:
    """Call the AI advisor for a plain-text 1-2 sentence reply, or None.

    H27: on failure the session must be rolled back before the caller continues
    to the next item, otherwise a half-applied transaction poisons every
    subsequent create in the same batch. The WhatsApp copy skipped the rollback.
    """
    try:
        from mihomes.services.ai.orchestrator import ask
        full_prompt = (
            f"{prompt}\n\n"
            "Reply in 1-2 sentences maximum. Plain text only — no bullet points, "
            "no headers, no markdown. Be direct and practical. "
            "If you are not confident in your answer or do not have enough information, "
            "respond with exactly: NO_RESPONSE"
        )
        response = ask(session, full_prompt, role=role, property_slug=property_slug)
        text = strip_markdown(response.text.strip())
        text = re.sub(r"\n{2,}", " ", text)
        if not text or text.upper() == "NO_RESPONSE":
            return None
        return text
    except Exception as e:
        logger.warning("AI response failed: %s", e)
        try:
            session.rollback()
        except Exception:
            logger.exception("ai_response: rollback failed")
        return None


def issue_expert_reply(session: Session, title: str, description: str | None, property_slug: str | None) -> str | None:
    context = description or title
    prompt = (
        f"A maintenance issue was just reported at a property: '{title}'. "
        f"Details: {context}. "
        "As a maintenance expert, give a brief practical assessment: "
        "what this likely requires and what the immediate next step should be."
    )
    return ai_response(session, prompt, role="maintenance", property_slug=property_slug)


# --------------------------------------------------------------------------- #
# Estate context + analyze_messages (shared; superset schema + image batching)
# --------------------------------------------------------------------------- #
def build_estate_context(session: Session, property_slug: str | None) -> str:
    if not property_slug:
        return ""
    try:
        prop = resolve_identifier(session, Property, property_slug)
    except Exception:
        return ""

    lines = [f"## Estate Context — {prop.name}"]
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    open_statuses = [
        IssueStatus.REPORTED, IssueStatus.ASSESSED,
        IssueStatus.SCHEDULED, IssueStatus.IN_PROGRESS,
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
            age_days = (now_naive - issue.created_at).days
            lines.append(f"- [{issue.severity.value}] {issue.title} — open {age_days}d (slug: {issue.slug})")
    else:
        lines.append("\n### Open Issues\n- None")

    cutoff = now_naive - timedelta(days=30)
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


def _load_image_attachments(messages: list[dict]) -> tuple[list[Attachment], list[str]]:
    """Return (image attachments, formatted conversation lines)."""
    formatted: list[str] = []
    image_attachments: list[Attachment] = []
    for msg in messages:
        sender = msg.get("senderName", "Unknown")
        text = (msg.get("text") or "").strip()
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
    return image_attachments, formatted


def analyze_messages(
    session: Session,
    messages: list[dict],
    property_name: str | None = None,
    property_slug: str | None = None,
) -> dict:
    """Analyze gateway messages and extract actionable items.

    Returns {'items': [...], 'skipped': [...]}. Message dicts use the shared
    internal format (`jid`, `senderName`, `text`, `hasMedia`, `mediaPath`,
    `propertySlug`) common to both the Telegram and WhatsApp pipelines.
    """
    if not messages:
        return {"items": [], "skipped": []}

    image_attachments, formatted = _load_image_attachments(messages)
    conversation_text = "\n".join(formatted)

    estate_context = build_estate_context(session, property_slug)
    property_header = f"Property: {property_name}" if property_name else ""
    context_parts = [p for p in [property_header, estate_context] if p]
    context = "\n\n".join(context_parts) if context_parts else ""

    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    model = get_ai_model(session, provider_name)
    provider = get_provider(provider_name, api_key, model=model)

    max_images = getattr(provider, "max_images_per_request", None)

    if image_attachments and max_images and len(image_attachments) > max_images:
        # Provider has a per-request image limit — batch and merge results.
        # book_addition / asset_addition items merge by accumulating their
        # sub-arrays (books / assets) so no entries are lost to deduplication.
        all_items: list[dict] = []
        all_skipped: list[dict] = []
        seen_titles: set[tuple] = set()
        array_field = {"book_addition": "books", "asset_addition": "assets"}
        merged: dict[str, dict] = {}

        def _merge_item(item: dict) -> None:
            cat = item.get("category", "")
            field = array_field.get(cat)
            if field:
                if cat not in merged:
                    merged[cat] = dict(item)
                else:
                    merged[cat][field] = merged[cat].get(field) or []
                    merged[cat][field].extend(item.get(field) or [])
                    if not merged[cat].get("room") and item.get("room"):
                        merged[cat]["room"] = item["room"]
            else:
                key = (cat, item.get("title", "").lower())
                if key not in seen_titles:
                    seen_titles.add(key)
                    all_items.append(item)

        for i in range(0, len(image_attachments), max_images):
            batch = image_attachments[i:i + max_images]
            batch_result = provider.structured_output(
                SYSTEM_PROMPT, conversation_text, REVIEW_SCHEMA,
                context_data=context or None, attachments=batch,
            )
            for item in batch_result.get("items", []):
                _merge_item(item)
            all_skipped.extend(batch_result.get("skipped", []))

        if formatted:
            text_result = provider.structured_output(
                SYSTEM_PROMPT, conversation_text, REVIEW_SCHEMA,
                context_data=context or None, attachments=None,
            )
            for item in text_result.get("items", []):
                _merge_item(item)

        all_items.extend(merged.values())
        return {"items": all_items, "skipped": all_skipped}

    return provider.structured_output(
        SYSTEM_PROMPT, conversation_text, REVIEW_SCHEMA,
        context_data=context or None,
        attachments=image_attachments or None,
    )


# --------------------------------------------------------------------------- #
# Approval routing (H24/H25) — shared, gateway supplies `is_approver`
# --------------------------------------------------------------------------- #
def handle_approval_messages(
    session: Session,
    messages: list[dict],
    *,
    adapter: GatewayAdapter,
    is_approver: Callable[[dict], bool],
) -> list[dict]:
    """Consume APPROVE/DENY commands from the approver; return the rest.

    H24: approval replies must be checked before any propertySlug filter drops
    them (an approver DM has no linked property). Callers run this first and pass
    the returned list on to `dispatch_items`.
    """
    from mihomes.services.staff_pto import approve_pto, deny_pto, notify_staff

    remaining: list[dict] = []
    for message in messages:
        if not is_approver(message):
            remaining.append(message)
            continue

        text = (message.get("text") or "").strip()
        approve_match = re.match(r"APPROVE\s+(\d+)", text, re.IGNORECASE)
        deny_match = re.match(r"DENY\s+(\d+)(?:\s+(.+))?", text, re.IGNORECASE)
        reply_target = message.get("jid")

        if approve_match:
            req_id = approve_match.group(1)
            try:
                req = approve_pto(session, int(req_id), decided_by="approver")
                notify_staff(session, req)
                if reply_target:
                    adapter.send(reply_target, f"PTO approved for {req.staff.name} — {', '.join(req.dates)} ✓")
            except Exception:
                # M25: the approver must hear that it failed — but only a generic
                # message; the exception detail is logged locally, never sent.
                logger.exception("handle_approval_messages: approve failed for #%s", req_id)
                if reply_target:
                    adapter.send(reply_target, f"Could not approve #{req_id} — please try again or log manually.")
            continue

        if deny_match:
            req_id = deny_match.group(1)
            try:
                req = deny_pto(
                    session, int(req_id),
                    decided_by="approver", reason=deny_match.group(2) or None,
                )
                notify_staff(session, req)
                if reply_target:
                    adapter.send(reply_target, f"PTO denied for {req.staff.name} — {', '.join(req.dates)}")
            except Exception:
                logger.exception("handle_approval_messages: deny failed for #%s", req_id)
                if reply_target:
                    adapter.send(reply_target, f"Could not deny #{req_id} — please try again or log manually.")
            continue

        # From the approver but not a command — let it flow through analysis.
        remaining.append(message)

    return remaining


# Categories that create money/PTO/resolution records — gated behind a trusted
# sender so a random group member can't move the estate's books (M27). Plain
# issue/task/question reporting stays open to everyone.
SENSITIVE_CATEGORIES = frozenset({
    "expense_log", "pto_request", "issue_resolution",
    "work_order_request", "task_completion",
})


def group_by_target(messages: list[dict]) -> dict[str, list[dict]]:
    """Group messages by their originating chat jid, preserving order (M26).

    A batch can span multiple chats of the same property; each chat must get its
    own replies rather than everything landing on the first message's jid.
    Messages without a jid are dropped (there is nowhere to reply).
    """
    groups: dict[str, list[dict]] = {}
    for m in messages:
        jid = m.get("jid")
        if not jid:
            continue
        groups.setdefault(jid, []).append(m)
    return groups


def is_trusted_sender(session: Session, message: dict, *, gateway: str) -> bool:
    """True if the sender may trigger sensitive (money/PTO) actions (M27).

    Trust is granted to (a) any configured allowlist entry for the gateway, or
    (b) a known staff member matched by phone (WhatsApp) or sender id (Telegram).
    An empty allowlist does NOT trust everyone — staff-matching is the floor.
    """
    from mihomes.services.config_service import get_config

    raw_sender = str(message.get("sender") or "")
    if not raw_sender:
        return False

    allow = get_config(session, f"{gateway}.sender_allowlist") or ""
    allow_ids = {a.strip() for a in allow.split(",") if a.strip()}

    if gateway == "whatsapp":
        phone = sender_phone(message)
        norm_allow = {a.replace("+", "").replace("-", "").replace(" ", "") for a in allow_ids}
        if phone and any(phone in a or a in phone for a in norm_allow if a):
            return True
        if phone:
            from mihomes.models.staff import Staff
            for s in session.query(Staff).filter(Staff.whatsapp_phone.isnot(None)).all():
                s_phone = (s.whatsapp_phone or "").replace("+", "").replace("-", "").replace(" ", "")
                if s_phone and (s_phone in phone or phone in s_phone):
                    return True
        return False

    # Telegram: staff have no stored telegram id, so trust is the allowlist plus
    # the configured approver id (the owner is always trusted).
    if raw_sender in allow_ids:
        return True
    approver_id = get_config(session, "telegram.pto_approver_id")
    return bool(approver_id) and raw_sender == str(approver_id).strip()


# --------------------------------------------------------------------------- #
# Dispatch — the full category loop, shared verbatim by both gateways (M24)
# --------------------------------------------------------------------------- #
def dispatch_items(
    session: Session,
    items: list[dict],
    *,
    adapter: GatewayAdapter,
    reply_target: str,
    messages: list[dict],
    property_slug: str | None,
    resolve_reporter: Callable[[dict], int | None],
    sender_trusted: bool = True,
) -> dict:
    """Act on every extracted item, sending confirmations via `adapter`.

    `resolve_reporter(item)` is the one genuinely gateway-specific step:
    WhatsApp can match a reporter by phone number, Telegram can only match by
    name. Everything else is identical across gateways.

    `sender_trusted` (M27) gates the sensitive categories: when False, an item in
    `SENSITIVE_CATEGORIES` is skipped (not logged) so an untrusted group member
    can't create money/PTO/resolution records.
    """
    send = adapter.send
    logged = 0
    replied = 0
    errors: list[str] = []
    seen_categories: set[str] = set()

    for item in items:
        category = item.get("category", "")
        seen_categories.add(category)
        title = item.get("title", "Unknown")
        prop = item.get("property_slug") or property_slug

        # M27: money/PTO/resolution actions require a trusted sender.
        if not sender_trusted and category in SENSITIVE_CATEGORIES:
            logger.warning(
                "dispatch_items: skipping sensitive '%s' from untrusted sender", category
            )
            continue

        # --- Questions ---
        if category == "question":
            question_text = item.get("description") or title
            answer = ai_response(session, question_text, role="estate_manager", property_slug=prop or property_slug)
            if answer:
                try:
                    send(reply_target, answer)
                    replied += 1
                except Exception as e:
                    errors.append(f"Failed to send answer: {e}")
            continue

        # --- PTO requests ---
        if category == "pto_request":
            reporter = item.get("reported_by") or item.get("assigned_to")
            pto_dates = item.get("pto_dates") or []
            if not reporter or not pto_dates:
                continue
            try:
                from mihomes.services.staff_pto import create_pto_request, notify_approver
                staff = session.query(Staff).filter(
                    Staff.name.ilike(f"%{escape_like(reporter)}%", escape="\\")
                ).first()
                if not staff:
                    continue
                req = create_pto_request(session, str(staff.id), pto_dates, notes=item.get("description"))
                logged += 1
                notify_approver(session, req)
                dates_str = ", ".join(pto_dates)
                warning = f"\n{req.coverage_warning}" if req.coverage_warning else ""
                try:
                    send(reply_target, f"PTO request logged for {staff.name} — {dates_str}. Pending approval.{warning}")
                    replied += 1
                except Exception as e:
                    errors.append(f"Failed to confirm PTO: {e}")
            except Exception as e:
                errors.append(f"Failed to log PTO request: {e}")
            continue

        # --- Supply needs ---
        if category == "supply_need":
            if not prop:
                continue
            try:
                from mihomes.services.consumable import update_stock
                update_stock(
                    session, title, prop,
                    quantity_in_stock=item.get("quantity_in_stock"),
                    quantity_to_order=item.get("quantity_to_order"),
                    unit=item.get("unit"),
                    updated_by=item.get("reported_by") or adapter.label,
                )
                logged += 1
                try:
                    send(reply_target, f'"{title}" inventory updated ✓')
                    replied += 1
                except Exception as e:
                    errors.append(f"Failed to confirm supply update: {e}")
            except Exception as e:
                errors.append(f"Failed to log supply '{title}': {e}")
            continue

        # --- Task completion ---
        if category == "task_completion":
            try:
                from mihomes.models.task import Task, TaskStatus
                from mihomes.services.task import complete_task
                task_ref = item.get("task_ref") or title
                prop_obj = session.query(Property).filter(Property.slug == prop).first() if prop else None
                matched_task = None
                if prop_obj and task_ref:
                    matched_task = (
                        session.query(Task)
                        .filter(
                            Task.property_id == prop_obj.id,
                            Task.title.ilike(f"%{escape_like(task_ref)}%", escape="\\"),
                            Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
                        )
                        .order_by(Task.created_at.desc())
                        .first()
                    )
                if matched_task:
                    complete_task(session, matched_task.slug, notes=item.get("description"))
                    logged += 1
                    send(reply_target, f'"{matched_task.title}" marked complete ✓')
                else:
                    send(reply_target, "Noted ✓")
                replied += 1
            except Exception as e:
                errors.append(f"Failed to complete task: {e}")
            continue

        # --- Book addition ---
        if category == "book_addition":
            if not prop:
                continue
            books_data = item.get("books") or []
            if not books_data:
                try:
                    send(reply_target, "No books identified — send photos of the covers or list titles to add.")
                    replied += 1
                except Exception:
                    logger.exception("dispatch_items: book_addition empty reply failed")
                continue
            from mihomes.models.book import BookCondition
            from mihomes.services.book import create_book
            room_slug = resolve_room(session, item.get("room"), prop)
            created_titles, book_errors = [], []
            for b in books_data:
                b_title = (b.get("title") or "").strip()
                if not b_title:
                    continue
                try:
                    try:
                        condition = BookCondition(b.get("condition", "good"))
                    except ValueError:
                        condition = BookCondition.GOOD
                    book = create_book(
                        session, title=b_title, property_id_or_slug=prop,
                        space_id_or_slug=room_slug,
                        author=b.get("author"), genre=b.get("genre"),
                        isbn=b.get("isbn"), condition=condition,
                    )
                    created_titles.append(book.title)
                except Exception as e:
                    book_errors.append(f"'{b_title}': {e}")
            if created_titles:
                logged += len(created_titles)
                lines = [f"Added {len(created_titles)} book(s) to library ✓"]
                lines.extend(f"• {t}" for t in created_titles[:20])
                if len(created_titles) > 20:
                    lines.append(f"  …and {len(created_titles) - 20} more")
                try:
                    send(reply_target, "\n".join(lines))
                    replied += 1
                except Exception as e:
                    errors.append(f"Failed to confirm book addition: {e}")
            errors.extend(book_errors)
            continue

        # --- Asset addition ---
        if category == "asset_addition":
            if not prop:
                continue
            assets_data = item.get("assets") or []
            if not assets_data:
                try:
                    send(reply_target, "No assets identified — send photos or describe items to add.")
                    replied += 1
                except Exception:
                    logger.exception("dispatch_items: asset_addition empty reply failed")
                continue
            from mihomes.models.asset import AssetCondition, AssetType
            from mihomes.services.asset import create_asset
            room_slug = resolve_room(session, item.get("room"), prop)
            created_names, asset_errors = [], []
            for a in assets_data:
                a_name = (a.get("name") or "").strip()
                if not a_name:
                    continue
                try:
                    try:
                        a_type = AssetType(a.get("asset_type", "equipment"))
                    except ValueError:
                        a_type = AssetType.EQUIPMENT
                    try:
                        a_cond = AssetCondition(a.get("condition", "good"))
                    except ValueError:
                        a_cond = AssetCondition.GOOD
                    asset = create_asset(
                        session, name=a_name, asset_type=a_type,
                        property_id_or_slug=prop, space_id_or_slug=room_slug,
                        make=a.get("make"), model_name=a.get("model"),
                        condition=a_cond,
                        purchase_price=float(a["estimated_value"]) if a.get("estimated_value") else None,
                        notes=a.get("note"),
                    )
                    created_names.append(asset.name)
                except Exception as e:
                    asset_errors.append(f"'{a_name}': {e}")
            if created_names:
                logged += len(created_names)
                lines = [f"Added {len(created_names)} asset(s) ✓"]
                lines.extend(f"• {n}" for n in created_names[:20])
                try:
                    send(reply_target, "\n".join(lines))
                    replied += 1
                except Exception as e:
                    errors.append(f"Failed to confirm asset addition: {e}")
            errors.extend(asset_errors)
            continue

        # --- Issue resolution ---
        if category == "issue_resolution":
            if not prop:
                continue
            try:
                from mihomes.services.issue import resolve_issue
                issue_ref = item.get("issue_ref") or item.get("related_asset") or title
                prop_obj = session.query(Property).filter(Property.slug == prop).first()
                matched_issue = None
                if prop_obj and issue_ref:
                    matched_issue = (
                        session.query(Issue)
                        .filter(
                            Issue.property_id == prop_obj.id,
                            Issue.title.ilike(f"%{escape_like(issue_ref)}%", escape="\\"),
                            Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED]),
                        )
                        .order_by(Issue.created_at.desc())
                        .first()
                    )
                if matched_issue:
                    resolve_issue(session, matched_issue.slug, notes=item.get("resolution_notes") or item.get("description"))
                    logged += 1
                    send(reply_target, f'"{matched_issue.title}" resolved ✓')
                else:
                    send(reply_target, "Noted ✓ (couldn't match an open issue — resolve manually if needed)")
                replied += 1
            except Exception as e:
                errors.append(f"Failed to resolve issue: {e}")
            continue

        # --- Work order request ---
        if category == "work_order_request":
            if not prop:
                continue
            try:
                from mihomes.services.work_order import create_work_order
                vendor = resolve_vendor(session, item.get("vendor_name"))
                wo = create_work_order(
                    session, title=title, property_id_or_slug=prop,
                    description=item.get("description"),
                    vendor_id_or_slug=vendor.slug if vendor else None,
                    estimated_cost=item.get("amount"),
                )
                logged += 1
                send(reply_target, f'Work order "{wo.title}" created ✓')
                replied += 1
            except Exception as e:
                errors.append(f"Failed to create work order '{title}': {e}")
            continue

        # --- Appointment request ---
        if category == "appointment_request":
            if not prop:
                continue
            try:
                from mihomes.models.appointment import AppointmentType
                from mihomes.services.appointment import create_appointment
                appt_date = parse_event_date(item.get("date") or item.get("timestamp"))
                vendor = resolve_vendor(session, item.get("vendor_name"))
                try:
                    appt_type = AppointmentType(item.get("appointment_type") or "vendor_visit")
                except (ValueError, AttributeError):
                    appt_type = AppointmentType.VENDOR_VISIT
                appt = create_appointment(
                    session, title=title, property_id_or_slug=prop,
                    appt_date=appt_date or date.today(),
                    vendor_id=vendor.id if vendor else None,
                    appointment_type=appt_type,
                    notes=item.get("description"),
                )
                logged += 1
                date_str = appt.date.strftime("%b %d") if appt.date else "TBD"
                send(reply_target, f'"{title}" scheduled for {date_str} ✓')
                replied += 1
            except Exception as e:
                errors.append(f"Failed to schedule appointment '{title}': {e}")
            continue

        # --- Expense log ---
        if category == "expense_log":
            if not prop:
                continue
            amount = item.get("amount")
            if not amount:
                try:
                    send(reply_target, "Expense noted but no amount found — log manually.")
                    replied += 1
                except Exception:
                    logger.exception("dispatch_items: expense_log no-amount reply failed")
                continue
            try:
                from mihomes.services.budget import add_transaction
                vendor = resolve_vendor(session, item.get("vendor_name"))
                tx_date = parse_event_date(item.get("date") or item.get("timestamp")) or date.today()
                add_transaction(
                    session,
                    amount=float(amount),
                    property_id_or_slug=prop,
                    category=item.get("expense_category") or "operations",
                    tx_date=tx_date,
                    vendor_id_or_slug=vendor.slug if vendor else None,
                    vendor_name=item.get("vendor_name") if not vendor else None,
                    description=title,
                    source=adapter.label.lower(),
                )
                logged += 1
                send(reply_target, f"Expense ${float(amount):,.2f} logged ✓")
                replied += 1
            except Exception as e:
                errors.append(f"Failed to log expense '{title}': {e}")
            continue

        # --- Note addition ---
        if category == "note_addition":
            note_text = item.get("note_text") or item.get("description")
            entity_type_val = item.get("entity_type") or "issue"
            entity_ref_val = item.get("entity_ref") or item.get("issue_ref") or item.get("task_ref")
            if not note_text or not entity_ref_val:
                continue
            try:
                from mihomes.services.note import add_note
                add_note(session, entity_ref=f"{entity_type_val}:{entity_ref_val}", content=note_text)
                logged += 1
                send(reply_target, "Note added ✓")
                replied += 1
            except Exception as e:
                errors.append(f"Failed to add note: {e}")
            continue

        # --- Issues, tasks, vendor activity ---
        if category not in ("issue", "task", "vendor_activity"):
            continue
        if not prop:
            continue

        event_date = parse_event_date(item.get("timestamp"))
        try:
            if category == "issue":
                _create_issue_with_photos(session, item, prop, title, messages, resolve_reporter)
                logged += 1
            elif category == "task":
                from mihomes.services.task import create_task
                assignee = resolve_staff_slug(session, item.get("assigned_to"))
                create_task(session, title, prop, description=item.get("description"), assignee_id_or_slug=assignee)
                logged += 1
            elif category == "vendor_activity":
                from mihomes.services.task import create_task
                assignee = resolve_staff_slug(session, item.get("assigned_to"))
                create_task(session, title, prop, description=item.get("description"),
                            due_date=event_date, assignee_id_or_slug=assignee)
                logged += 1
        except Exception as e:
            errors.append(f"Failed to create '{title}': {e}")
            continue

        if category == "issue":
            expert_note = issue_expert_reply(session, title, item.get("description"), prop)
            confirmation = f'"{title}" logged ✓\n\n{expert_note}' if expert_note else f'"{title}" logged ✓'
        else:
            confirmation = f'"{title}" logged ✓'
        try:
            send(reply_target, confirmation)
            replied += 1
        except Exception as e:
            errors.append(f"Failed to send confirmation for '{title}': {e}")

    # L14: surface any category the model produced that we don't dispatch (and
    # isn't a deliberate no-op) so a schema/dispatch drift is visible, not silent.
    undispatched = seen_categories - DISPATCHABLE_CATEGORIES - {"informational", ""}
    for cat in sorted(undispatched):
        errors.append(f"Unhandled category '{cat}' — no dispatch branch")

    return {"replied": replied, "logged": logged, "errors": errors}


def _create_issue_with_photos(session, item, prop, title, messages, resolve_reporter):
    """Create an issue and attach any batch photos as Documents (H26 path)."""
    from mihomes.models.document import DocumentType
    from mihomes.models.issue import IssueSeverity
    from mihomes.services.document import create_document
    from mihomes.services.issue import create_issue

    try:
        sev = IssueSeverity(item.get("severity", "medium"))
    except ValueError:
        sev = IssueSeverity.MEDIUM
    reporter_id = resolve_reporter(item)
    room_slug = resolve_room(session, item.get("room"), prop)
    issue = create_issue(
        session, title, prop,
        severity=sev,
        description=item.get("description"),
        reported_by_id=reporter_id,
        space_id_or_slug=room_slug,
    )

    dest_dir = uploads_dir()
    import shutil
    for idx, msg in enumerate(messages):
        media_path = msg.get("mediaPath")
        if not media_path or not os.path.isfile(media_path):
            continue
        try:
            mime = mimetypes.guess_type(media_path)[0] or "image/jpeg"
            if not mime.startswith("image/"):
                continue
            ext = Path(media_path).suffix.lower() or ".jpg"
            fname = f"{uuid.uuid4().hex}{ext}"
            shutil.copy2(media_path, dest_dir / fname)
            create_document(
                session,
                title=f"Photo {idx + 1} — {title}",
                file_path=f"/static/uploads/{fname}",
                document_type=DocumentType.OTHER,
                entity_type="issue",
                entity_id=issue.id,
            )
        except Exception:
            logger.exception("_create_issue_with_photos: suppressed exception")
    return issue
