"""WhatsApp responder — logs issues/tasks and answers questions via AI."""

import base64
import mimetypes
import os
import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from mihomes.services.gateways.whatsapp.client import WhatsAppClient
from mihomes.services.gateways.whatsapp.review import analyze_messages


def handle_inventory_scan(
    session: Session,
    messages: list[dict],
    property_slug: str,
    reply_jid: str,
    client: WhatsAppClient,
) -> dict:
    """Process photos from the inventory group — identify and create assets."""
    from mihomes.models.asset import AssetCondition, AssetType
    from mihomes.models.property import Property
    from mihomes.models.space import Space
    from mihomes.services.ai.assessors import parse_room_scan
    from mihomes.services.ai.file_processor import Attachment
    from mihomes.services.asset import create_asset

    # Collect images and room name from captions
    image_attachments: list[Attachment] = []
    room_name: str | None = None

    for msg in messages:
        text = (msg.get("text") or "").strip()
        if text and not room_name:
            room_name = text

        media_path = msg.get("mediaPath")
        if msg.get("hasMedia") and media_path and os.path.isfile(media_path):
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
            except OSError:
                pass

    if not image_attachments:
        client.send_group_message(
            reply_jid,
            "🏠 No photos found — send a photo of the room to scan it.\n"
            "_Tip: add the room name as a caption (e.g. 'Master Bedroom')_",
        )
        return {"replied": 1, "logged": 0, "errors": []}

    # Fuzzy-match room name to a Space
    space_slug: str | None = None
    if room_name and property_slug:
        prop = session.query(Property).filter(Property.slug == property_slug).first()
        if prop:
            spaces = session.query(Space).filter(Space.property_id == prop.id).all()
            room_lower = room_name.lower()
            for space in spaces:
                if room_lower in space.name.lower() or space.name.lower() in room_lower:
                    space_slug = space.slug
                    room_name = space.name
                    break

    room_label = f" {room_name}" if room_name else ""
    client.send_group_message(reply_jid, f"🏠 Scanning{room_label}... ⏳")

    try:
        items = parse_room_scan(session, image_attachments, room_name=room_name)
    except Exception as e:
        client.send_group_message(reply_jid, f"🏠 ⚠️ Scan failed — {e}")
        return {"replied": 1, "logged": 0, "errors": [str(e)]}

    if not items:
        client.send_group_message(reply_jid, "🏠 No trackable assets identified in the photo(s).")
        return {"replied": 1, "logged": 0, "errors": []}

    created, errors = [], []
    for item in items:
        try:
            name = item.get("name") or "Unknown Asset"
            try:
                asset_type = AssetType(item.get("asset_type", "equipment"))
            except ValueError:
                asset_type = AssetType.EQUIPMENT
            try:
                condition = AssetCondition((item.get("condition") or "good").lower())
            except ValueError:
                condition = AssetCondition.GOOD

            value = item.get("estimated_value")
            asset = create_asset(
                session, name=name, asset_type=asset_type,
                property_id_or_slug=property_slug,
                space_id_or_slug=space_slug,
                make=item.get("make"),
                model_name=item.get("model"),
                condition=condition,
                purchase_price=float(value) if value else None,
                notes=item.get("notes"),
            )
            line = f"• {asset.name}"
            if value:
                line += f" (~${float(value):,.0f})"
            created.append(line)
        except Exception as e:
            errors.append(str(e))

    lines = [f"🏠 Added {len(created)} item(s){' in ' + room_name if room_name else ''} ✓"]
    lines.extend(created[:20])
    if len(created) > 20:
        lines.append(f"  …and {len(created) - 20} more")
    client.send_group_message(reply_jid, "\n".join(lines))

    return {"replied": 2, "logged": len(created), "errors": errors}


def _parse_event_date(timestamp_str: str | None) -> date | None:
    """Parse an AI-extracted timestamp string into a date, or return None."""
    if not timestamp_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(timestamp_str, fmt).date()
        except ValueError:
            continue
    return None


def _strip_markdown(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'[-•]\s+', '', text)
    return text.strip()


def _ai_response(session: Session, prompt: str, role: str, property_slug: str | None) -> str | None:
    """Call the AI advisor and return a plain-text 1-2 sentence response, or None on failure."""
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
        text = _strip_markdown(response.text.strip())
        text = re.sub(r'\n{2,}', ' ', text)
        if not text or text.upper() == "NO_RESPONSE":
            return None
        return text
    except Exception as e:
        import logging
        logging.getLogger("mihomes.whatsapp").warning("AI response failed: %s", e)
        return None


def _issue_expert_reply(session: Session, title: str, description: str | None, property_slug: str | None) -> str | None:
    """Get a maintenance expert assessment for a logged issue."""
    context = description or title
    prompt = (
        f"A maintenance issue was just reported at a property: '{title}'. "
        f"Details: {context}. "
        "As a maintenance expert, give a brief practical assessment: "
        "what this likely requires and what the immediate next step should be."
    )
    return _ai_response(session, prompt, role="maintenance", property_slug=property_slug)


def _answer_question(session: Session, question: str, property_slug: str | None) -> str | None:
    """Answer a home-related question using the estate manager role."""
    return _ai_response(session, question, role="estate_manager", property_slug=property_slug)


def _resolve_staff_slug(session: Session, name: str | None) -> str | None:
    """Find a staff member by partial name match, return their slug."""
    if not name:
        return None
    from mihomes.models.staff import Staff
    staff = session.query(Staff).filter(Staff.name.ilike(f"%{name}%")).first()
    return staff.slug if staff else None


def _handle_approval_message(session: Session, message: dict, client) -> bool:
    """
    Check if a message from the approver is an APPROVE/DENY command.
    Returns True if handled, False if it should flow through normal analysis.
    """
    import re

    from mihomes.services.config_service import get_config
    from mihomes.services.staff_pto import approve_pto, deny_pto, notify_staff

    approver_phone = (
        get_config(session, "staff.pto_approver_phone")
        or get_config(session, "owner.whatsapp_phone")
    )
    if not approver_phone:
        return False

    sender_phone = message.get("senderPhone", "")
    if not sender_phone or approver_phone.replace("+", "").replace("-", "") not in sender_phone.replace("+", "").replace("-", ""):
        return False

    text = (message.get("text") or "").strip().upper()
    approve_match = re.match(r"APPROVE\s+(\d+)", text)
    deny_match = re.match(r"DENY\s+(\d+)(?:\s+(.+))?", text, re.IGNORECASE)

    if approve_match:
        req_id = int(approve_match.group(1))
        try:
            req = approve_pto(session, req_id, decided_by="approver")
            notify_staff(session, req)
            reply_jid = message.get("jid")
            if reply_jid:
                client.send_group_message(reply_jid, f"🏠 PTO approved for {req.staff.name} — {', '.join(req.dates)} ✓")
        except Exception:
            pass
        return True

    if deny_match:
        req_id = int(deny_match.group(1))
        reason = deny_match.group(2) or None
        try:
            req = deny_pto(session, req_id, decided_by="approver", reason=reason)
            notify_staff(session, req)
            reply_jid = message.get("jid")
            if reply_jid:
                client.send_group_message(reply_jid, f"🏠 PTO denied for {req.staff.name} — {', '.join(req.dates)}")
        except Exception:
            pass
        return True

    return False


def process_and_respond(
    session: Session,
    messages: list[dict],
    property_slug: str | None = None,
) -> dict:
    """
    Analyze messages, create issues/tasks, answer questions, handle PTO.

    - Issues: log + send '🏠 "title" logged ✓\\n\\n[maintenance expert assessment]'
    - Tasks/supply_needs with date: log + create event + send '🏠 scheduled "title" ✓'
    - Tasks/supply_needs without date: log + send '🏠 "title" logged ✓'
    - Vendor activity: create event + send '🏠 scheduled "title" ✓'
    - Questions about the home: send '🏠 [AI estate manager answer]'
    - PTO requests: log as pending + notify approver via direct message
    - Informational/irrelevant: no response

    Returns dict with: logged, replied, errors.
    """
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    import logging

    from mihomes.services.ai.provider import AIProviderError
    client = WhatsAppClient()
    _log = logging.getLogger("mihomes.whatsapp")

    # Pre-check: handle APPROVE/DENY replies from the approver before AI analysis
    remaining_messages = []
    for msg in messages:
        if not _handle_approval_message(session, msg, client):
            remaining_messages.append(msg)
    messages = remaining_messages
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    # Resolve reply target early so we can report errors to the group
    reply_jid = next(
        (m["jid"] for m in messages if m.get("jid") and (m.get("propertySlug") or property_slug)),
        None,
    )
    if not reply_jid:
        return {"replied": 0, "logged": 0, "errors": ["No linked group JID found"]}

    # Route inventory group messages straight to the room scanner
    from mihomes.services.config_service import get_config
    inventory_jid = get_config(session, "whatsapp.inventory_group_jid")
    if inventory_jid and reply_jid == inventory_jid:
        inv_property = (messages[0].get("propertySlug") or property_slug or "belle-estate")
        return handle_inventory_scan(session, messages, inv_property, reply_jid, client)

    def _send_error_to_group(detail: str) -> None:
        try:
            client.send_group_message(
                reply_jid,
                f"🏠 ⚠️ Bot error — couldn't process message(s). Please log manually.\n_{detail}_",
            )
        except Exception:
            pass

    try:
        result = analyze_messages(session, messages, property_name=property_slug, property_slug=property_slug)
    except AIProviderError as e:
        _log.error("AI provider error during message analysis: %s", e)
        _send_error_to_group(str(e))
        return {"replied": 0, "logged": 0, "errors": [f"AI provider error: {e}"]}
    except Exception as e:
        _log.error("Unexpected error during message analysis: %s", e)
        _send_error_to_group(str(e))
        return {"replied": 0, "logged": 0, "errors": [f"Unexpected error: {e}"]}
    items = result.get("items", [])

    logged = 0
    replied = 0
    errors = []

    for item in items:
        category = item.get("category", "")
        title = item.get("title", "Unknown")
        prop = item.get("property_slug") or property_slug

        # --- Questions: answer with AI estate manager ---
        if category == "question":
            question_text = item.get("description") or title
            answer = _answer_question(session, question_text, prop or property_slug)
            if answer:
                try:
                    client.send_group_message(reply_jid, f"🏠 {answer}")
                    replied += 1
                except Exception as e:
                    errors.append(f"Failed to send answer: {e}")
            continue

        # --- PTO requests: log as pending + notify approver ---
        if category == "pto_request":
            reporter = item.get("reported_by") or item.get("assigned_to")
            pto_dates = item.get("pto_dates") or []
            if not reporter or not pto_dates:
                continue
            try:
                from mihomes.models.staff import Staff
                from mihomes.services.staff_pto import create_pto_request, notify_approver
                staff = session.query(Staff).filter(Staff.name.ilike(f"%{reporter}%")).first()
                if not staff:
                    continue
                req = create_pto_request(session, str(staff.id), pto_dates, notes=item.get("description"))
                logged += 1
                notify_approver(session, req)
                # Confirm in group chat
                dates_str = ", ".join(pto_dates)
                warning = f"\n⚠️ {req.coverage_warning}" if req.coverage_warning else ""
                try:
                    client.send_group_message(reply_jid, f"🏠 PTO request logged for {staff.name} — {dates_str}. Pending approval.{warning}")
                    replied += 1
                except Exception as e:
                    errors.append(f"Failed to confirm PTO: {e}")
            except Exception as e:
                errors.append(f"Failed to log PTO request: {e}")
            continue

        # --- Supply needs: silently update inventory, no group reply ---
        if category == "supply_need":
            if not prop:
                continue
            try:
                from mihomes.services.consumable import update_stock
                reporter = item.get("reported_by") or "WhatsApp"
                update_stock(
                    session, title, prop,
                    quantity_in_stock=item.get("quantity_in_stock"),
                    quantity_to_order=item.get("quantity_to_order"),
                    unit=item.get("unit"),
                    updated_by=reporter,
                )
                logged += 1
            except Exception as e:
                errors.append(f"Failed to log supply '{title}': {e}")
            continue  # No group chat reply for supply needs

        # --- Actionable items: log + confirm ---
        if category not in ("issue", "task", "vendor_activity"):
            continue

        if not prop:
            continue

        event_date = _parse_event_date(item.get("timestamp"))
        scheduled = event_date is not None

        try:
            if category == "issue":
                from mihomes.models.issue import IssueSeverity
                from mihomes.services.issue import create_issue
                sev_val = item.get("severity", "medium")
                try:
                    sev = IssueSeverity(sev_val)
                except ValueError:
                    sev = IssueSeverity.MEDIUM
                create_issue(session, title, prop, severity=sev, description=item.get("description"))
                logged += 1
            elif category == "task":
                from mihomes.services.task import create_task
                assignee = _resolve_staff_slug(session, item.get("assigned_to"))
                create_task(session, title, prop, description=item.get("description"), assignee_id_or_slug=assignee)
                logged += 1
                if scheduled:
                    from mihomes.services.event import create_event
                    create_event(session, title, prop, event_date, description=item.get("description"))
            elif category == "vendor_activity":
                # Always create a task — vendor visits for maintenance/repairs need tracking.
                # Also create a dated event if a specific date was mentioned.
                from mihomes.services.task import create_task
                assignee = _resolve_staff_slug(session, item.get("assigned_to"))
                new_task = create_task(session, title, prop, description=item.get("description"),
                                       due_date=event_date, assignee_id_or_slug=assignee)
                logged += 1
                # Auto-push vendor tasks to Google Calendar
                try:
                    from mihomes.services.calendar_sync import push_task_to_google
                    push_task_to_google(new_task, session=session)
                except Exception:
                    pass
                if scheduled:
                    from mihomes.services.event import create_event
                    create_event(session, title, prop, event_date, description=item.get("description"))
        except Exception as e:
            errors.append(f"Failed to create '{title}': {e}")
            continue  # Don't send confirmation if logging failed

        # Build confirmation message
        if category == "issue":
            expert_note = _issue_expert_reply(session, title, item.get("description"), prop)
            if expert_note:
                confirmation = f'🏠 "{title}" logged ✓\n\n{expert_note}'
            else:
                confirmation = f'🏠 "{title}" logged ✓'
        elif scheduled:
            confirmation = f'🏠 scheduled "{title}" ✓'
        else:
            confirmation = f'🏠 "{title}" logged ✓'

        try:
            client.send_group_message(reply_jid, confirmation)
            replied += 1
        except Exception as e:
            errors.append(f"Failed to send confirmation for '{title}': {e}")

    return {"replied": replied, "logged": logged, "errors": errors}
