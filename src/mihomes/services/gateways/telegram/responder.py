"""Telegram responder — logs issues/tasks and answers questions via AI."""

import base64
import json
import mimetypes
import os
import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from mihomes.services.gateways.telegram.client import TelegramClient, TelegramError
from mihomes.services.gateways.telegram.review import analyze_messages


def _get_client(session: Session) -> TelegramClient:
    from mihomes.services.config_service import get_config
    token = get_config(session, "telegram.bot_token")
    if not token:
        raise TelegramError("telegram.bot_token not configured — run: mihomes config set telegram.bot_token <token>")
    return TelegramClient(token)


def _get_chat_links(session: Session) -> dict:
    from mihomes.services.config_service import get_config
    raw = get_config(session, "telegram.chat_links") or "{}"
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}


def handle_inventory_scan(
    session: Session,
    messages: list[dict],
    property_slug: str,
    reply_chat_id: str,
    client: TelegramClient,
) -> dict:
    """Process photos from the inventory chat — identify and create assets."""
    from mihomes.models.asset import AssetCondition, AssetType
    from mihomes.models.property import Property
    from mihomes.models.space import Space
    from mihomes.services.ai.assessors import parse_room_scan
    from mihomes.services.ai.file_processor import Attachment
    from mihomes.services.asset import create_asset

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
        client.send_message(
            reply_chat_id,
            "No photos found — send a photo of the room to scan it.\n"
            "(Tip: add the room name as a caption, e.g. 'Master Bedroom')",
        )
        return {"replied": 1, "logged": 0, "errors": []}

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
    client.send_message(reply_chat_id, f"Scanning{room_label}... (please wait)")

    try:
        items = parse_room_scan(session, image_attachments, room_name=room_name)
    except Exception as e:
        client.send_message(reply_chat_id, f"Scan failed — {e}")
        return {"replied": 1, "logged": 0, "errors": [str(e)]}

    if not items:
        client.send_message(reply_chat_id, "No trackable assets identified in the photo(s).")
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

    lines = [f"Added {len(created)} item(s){' in ' + room_name if room_name else ''} ✓"]
    lines.extend(created[:20])
    if len(created) > 20:
        lines.append(f"  …and {len(created) - 20} more")
    client.send_message(reply_chat_id, "\n".join(lines))

    return {"replied": 2, "logged": len(created), "errors": errors}


def _parse_event_date(timestamp_str: str | None) -> date | None:
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
        logging.getLogger("mihomes.telegram").warning("AI response failed: %s", e)
        return None


def _issue_expert_reply(session: Session, title: str, description: str | None, property_slug: str | None) -> str | None:
    context = description or title
    prompt = (
        f"A maintenance issue was just reported at a property: '{title}'. "
        f"Details: {context}. "
        "As a maintenance expert, give a brief practical assessment: "
        "what this likely requires and what the immediate next step should be."
    )
    return _ai_response(session, prompt, role="maintenance", property_slug=property_slug)


def _answer_question(session: Session, question: str, property_slug: str | None) -> str | None:
    return _ai_response(session, question, role="estate_manager", property_slug=property_slug)


def _resolve_staff_slug(session: Session, name: str | None) -> str | None:
    if not name:
        return None
    from mihomes.models.staff import Staff
    staff = session.query(Staff).filter(Staff.name.ilike(f"%{name}%")).first()
    return staff.slug if staff else None


def _handle_approval_message(session: Session, message: dict, client: TelegramClient) -> bool:
    """Check if a message from the configured approver is an APPROVE/DENY command."""
    from mihomes.services.config_service import get_config
    from mihomes.services.staff_pto import approve_pto, deny_pto, notify_staff

    # Approver identified by Telegram user_id (stored as string in config)
    approver_id = get_config(session, "telegram.pto_approver_id")
    if not approver_id:
        return False

    sender_id = str(message.get("sender", ""))
    if sender_id != str(approver_id).strip():
        return False

    text = (message.get("text") or "").strip().upper()
    approve_match = re.match(r"APPROVE\s+(\d+)", text)
    deny_match = re.match(r"DENY\s+(\d+)(?:\s+(.+))?", text, re.IGNORECASE)

    if approve_match:
        req_id = int(approve_match.group(1))
        try:
            req = approve_pto(session, req_id, decided_by="approver")
            notify_staff(session, req)
            reply_chat_id = message.get("jid")
            if reply_chat_id:
                client.send_message(reply_chat_id, f"PTO approved for {req.staff.name} — {', '.join(req.dates)} ✓")
        except Exception:
            pass
        return True

    if deny_match:
        req_id = int(deny_match.group(1))
        reason = deny_match.group(2) or None
        try:
            req = deny_pto(session, req_id, decided_by="approver", reason=reason)
            notify_staff(session, req)
            reply_chat_id = message.get("jid")
            if reply_chat_id:
                client.send_message(reply_chat_id, f"PTO denied for {req.staff.name} — {', '.join(req.dates)}")
        except Exception:
            pass
        return True

    return False


def process_and_respond(
    session: Session,
    messages: list[dict],
    property_slug: str | None = None,
) -> dict:
    """Analyze Telegram messages, create issues/tasks, answer questions, handle PTO.

    - Issues: log + send '"title" logged ✓\\n\\n[maintenance expert assessment]'
    - Tasks/vendor_activity: log + send '"title" logged ✓'
    - Questions: answer with AI estate manager
    - PTO requests: log as pending + notify approver
    - Informational: no response

    Returns dict with: logged, replied, errors.
    """
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    import logging

    from mihomes.services.ai.provider import AIProviderError
    client = _get_client(session)
    _log = logging.getLogger("mihomes.telegram")

    # Pre-check: handle APPROVE/DENY from the approver before AI analysis
    remaining_messages = []
    for msg in messages:
        if not _handle_approval_message(session, msg, client):
            remaining_messages.append(msg)
    messages = remaining_messages
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    # Resolve reply target (chat_id stored in the "jid" slot)
    reply_chat_id = next(
        (m["jid"] for m in messages if m.get("jid") and (m.get("propertySlug") or property_slug)),
        None,
    )
    if not reply_chat_id:
        return {"replied": 0, "logged": 0, "errors": ["No linked chat found"]}

    # Route inventory chat straight to room scanner
    from mihomes.services.config_service import get_config
    inventory_chat_id = get_config(session, "telegram.inventory_chat_id")
    if inventory_chat_id and reply_chat_id == str(inventory_chat_id):
        inv_property = (messages[0].get("propertySlug") or property_slug or "belle-estate")
        return handle_inventory_scan(session, messages, inv_property, reply_chat_id, client)

    def _send_error_to_group(detail: str) -> None:
        try:
            client.send_message(
                reply_chat_id,
                f"Bot error — couldn't process message(s). Please log manually.\n{detail}",
            )
        except Exception:
            pass

    try:
        result = analyze_messages(session, messages, property_name=property_slug, property_slug=property_slug)
    except AIProviderError as e:
        _log.error("AI provider error: %s", e)
        _send_error_to_group(str(e))
        return {"replied": 0, "logged": 0, "errors": [f"AI provider error: {e}"]}
    except Exception as e:
        _log.error("Unexpected error: %s", e)
        _send_error_to_group(str(e))
        return {"replied": 0, "logged": 0, "errors": [f"Unexpected error: {e}"]}
    items = result.get("items", [])

    logged = 0
    replied = 0
    errors = []

    # Staff name → id lookup (Telegram has no phone numbers; name-match only)
    def _resolve_reporter(item: dict) -> int | None:
        name = item.get("reported_by")
        if name:
            from mihomes.models.staff import Staff
            s = session.query(Staff).filter(Staff.name.ilike(f"%{name}%")).first()
            if s:
                return s.id
        return None

    def _resolve_room(room_name: str | None, prop_slug: str | None) -> str | None:
        if not room_name or not prop_slug:
            return None
        from mihomes.models.property import Property
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
                    client.send_message(reply_chat_id, answer)
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
                dates_str = ", ".join(pto_dates)
                warning = f"\n{req.coverage_warning}" if req.coverage_warning else ""
                try:
                    client.send_message(reply_chat_id, f"PTO request logged for {staff.name} — {dates_str}. Pending approval.{warning}")
                    replied += 1
                except Exception as e:
                    errors.append(f"Failed to confirm PTO: {e}")
            except Exception as e:
                errors.append(f"Failed to log PTO request: {e}")
            continue

        # --- Supply needs: silently update inventory ---
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
                    updated_by=item.get("reported_by") or "Telegram",
                )
                logged += 1
            except Exception as e:
                errors.append(f"Failed to log supply '{title}': {e}")
            continue

        # --- Issues, tasks, vendor activity: log + confirm ---
        if category not in ("issue", "task", "vendor_activity"):
            continue

        if not prop:
            continue

        event_date = _parse_event_date(item.get("timestamp"))

        try:
            if category == "issue":
                from mihomes.models.document import DocumentType
                from mihomes.models.issue import IssueSeverity
                from mihomes.services.document import create_document
                from mihomes.services.issue import create_issue
                sev_val = item.get("severity", "medium")
                try:
                    sev = IssueSeverity(sev_val)
                except ValueError:
                    sev = IssueSeverity.MEDIUM
                reporter_id = _resolve_reporter(item)
                room_slug = _resolve_room(item.get("room"), prop)
                issue = create_issue(
                    session, title, prop,
                    severity=sev,
                    description=item.get("description"),
                    reported_by_id=reporter_id,
                    space_id_or_slug=room_slug,
                )
                # Save any photos as Documents linked to the issue
                import uuid as _uuid
                from pathlib import Path as _Path
                uploads_dir = _Path(__file__).parents[4] / "web" / "static" / "uploads"
                uploads_dir.mkdir(parents=True, exist_ok=True)
                for idx, msg in enumerate(messages):
                    media_path = msg.get("mediaPath")
                    if not media_path or not os.path.isfile(media_path):
                        continue
                    try:
                        mime = mimetypes.guess_type(media_path)[0] or "image/jpeg"
                        if not mime.startswith("image/"):
                            continue
                        ext = _Path(media_path).suffix.lower() or ".jpg"
                        fname = f"{_uuid.uuid4().hex}{ext}"
                        dest = uploads_dir / fname
                        import shutil as _shutil
                        _shutil.copy2(media_path, dest)
                        create_document(
                            session,
                            title=f"Photo {idx + 1} — {title}",
                            file_path=f"/static/uploads/{fname}",
                            document_type=DocumentType.OTHER,
                            entity_type="issue",
                            entity_id=issue.id,
                        )
                    except Exception:
                        pass
                logged += 1
            elif category == "task":
                from mihomes.services.task import create_task
                assignee = _resolve_staff_slug(session, item.get("assigned_to"))
                create_task(session, title, prop, description=item.get("description"), assignee_id_or_slug=assignee)
                logged += 1
            elif category == "vendor_activity":
                from mihomes.services.task import create_task
                assignee = _resolve_staff_slug(session, item.get("assigned_to"))
                create_task(session, title, prop, description=item.get("description"),
                            due_date=event_date, assignee_id_or_slug=assignee)
                logged += 1
        except Exception as e:
            errors.append(f"Failed to create '{title}': {e}")
            continue

        # Confirmation message
        if category == "issue":
            expert_note = _issue_expert_reply(session, title, item.get("description"), prop)
            confirmation = f'"{title}" logged ✓\n\n{expert_note}' if expert_note else f'"{title}" logged ✓'
        else:
            confirmation = f'"{title}" logged ✓'

        try:
            client.send_message(reply_chat_id, confirmation)
            replied += 1
        except Exception as e:
            errors.append(f"Failed to send confirmation for '{title}': {e}")

    return {"replied": replied, "logged": logged, "errors": errors}
