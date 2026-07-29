"""WhatsApp responder — logs issues/tasks and answers questions via AI.

Thin gateway shim over `services.gateways.review_common`: this module supplies
only the WhatsApp-specific pieces (the `WhatsAppClient` + "🏠 " reply prefix, the
inventory-scan route, approver identification by phone number, and phone-first
reporter matching). All analysis, dispatch, and photo-attach logic lives in the
shared core so it stays identical to the Telegram gateway.
"""

import base64
import logging
import mimetypes
import os

from sqlalchemy.orm import Session

from mihomes.services.gateways import review_common as rc
from mihomes.services.gateways.whatsapp.client import WhatsAppClient
from mihomes.services.gateways.whatsapp.review import analyze_messages
from mihomes.services.query_helpers import escape_like

logger = logging.getLogger("mihomes.whatsapp")


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
                notes=item.get("note"),
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


def _is_approver(session: Session, message: dict) -> bool:
    """True when a message is from the configured approver (matched by phone).

    H25: the phone is derived from the bridge's `sender` field (not the missing
    `senderPhone`) via the shared `sender_phone` helper.
    """
    from mihomes.services.config_service import get_config
    approver_phone = (
        get_config(session, "staff.pto_approver_phone")
        or get_config(session, "owner.whatsapp_phone")
    )
    if not approver_phone:
        return False
    norm_approver = approver_phone.replace("+", "").replace("-", "").replace(" ", "")
    sender = rc.sender_phone(message)
    return bool(sender) and (norm_approver in sender or sender in norm_approver)


def process_and_respond(
    session: Session,
    messages: list[dict],
    property_slug: str | None = None,
) -> dict:
    """Analyze messages, create issues/tasks, answer questions, handle PTO.

    Delegates all analysis and dispatch to `review_common`; only the client,
    "🏠 " reply prefix, inventory route, approver identity, and phone-based
    reporter matching are WhatsApp-specific.

    Returns dict with: logged, replied, errors.
    """
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    from mihomes.services.ai.provider import AIProviderError
    client = WhatsAppClient()

    # WhatsApp prefixes every outbound message with the house emoji.
    adapter = rc.GatewayAdapter(
        label="WhatsApp",
        send=lambda jid, text: client.send_group_message(jid, f"🏠 {text}"),
    )

    # Pre-check: handle APPROVE/DENY replies from the approver before analysis (H24).
    messages = rc.handle_approval_messages(
        session, messages,
        adapter=adapter,
        is_approver=lambda m: _is_approver(session, m),
    )
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    # M26: a batch can span several groups of the same property. Dispatch each
    # group independently so replies land in the group they came from.
    groups = rc.group_by_target(
        [m for m in messages if m.get("propertySlug") or property_slug]
    )
    if not groups:
        return {"replied": 0, "logged": 0, "errors": ["No linked group JID found"]}

    from mihomes.models.staff import Staff
    from mihomes.services.config_service import get_config
    inventory_jid = get_config(session, "whatsapp.inventory_group_jid")

    totals = {"replied": 0, "logged": 0, "errors": []}
    for reply_jid, group_msgs in groups.items():
        # Route inventory group messages straight to the room scanner
        if inventory_jid and reply_jid == inventory_jid:
            inv_property = (group_msgs[0].get("propertySlug") or property_slug or "belle-estate")
            r = handle_inventory_scan(session, group_msgs, inv_property, reply_jid, client)
            totals["replied"] += r.get("replied", 0)
            totals["logged"] += r.get("logged", 0)
            totals["errors"].extend(r.get("errors", []))
            continue

        def _send_error_to_group(detail: str, _jid=reply_jid) -> None:
            # M27: generic message to the group; detail is logged locally only.
            try:
                client.send_group_message(
                    _jid, "🏠 ⚠️ Bot error — couldn't process message(s). Please log manually."
                )
            except Exception:
                logger.exception("_send_error_to_group: suppressed exception")
            logger.error("responder group error (jid %s): %s", _jid, detail)

        try:
            result = analyze_messages(session, group_msgs, property_name=property_slug, property_slug=property_slug)
        except AIProviderError as e:
            logger.error("AI provider error during message analysis: %s", e)
            _send_error_to_group(str(e))
            totals["errors"].append(f"AI provider error: {e}")
            continue
        except Exception as e:
            logger.error("Unexpected error during message analysis: %s", e)
            _send_error_to_group(str(e))
            totals["errors"].append(f"Unexpected error: {e}")
            continue

        # Build phone → staff lookup for reporter identification (H25: phone from `sender`).
        phone_to_staff: dict[str, object] = {}
        for msg in group_msgs:
            phone = rc.sender_phone(msg)
            if phone and phone not in phone_to_staff:
                for s in session.query(Staff).filter(Staff.whatsapp_phone.isnot(None)).all():
                    s_phone = (s.whatsapp_phone or "").replace("+", "").replace("-", "").replace(" ", "")
                    if s_phone and (s_phone in phone or phone in s_phone):
                        phone_to_staff[phone] = s
                        break

        def _resolve_reporter(item: dict, _p2s=phone_to_staff, _msgs=group_msgs) -> int | None:
            """Match reporter by phone (from the batch) first, then by AI-extracted name."""
            for msg in _msgs:
                phone = rc.sender_phone(msg)
                if phone and phone in _p2s:
                    return _p2s[phone].id
            return rc.resolve_reporter_by_name(session, item.get("reported_by"))

        # M27: sensitive actions only for a group whose every sender is trusted.
        sender_trusted = bool(group_msgs) and all(
            rc.is_trusted_sender(session, m, gateway="whatsapp") for m in group_msgs
        )

        r = rc.dispatch_items(
            session, result.get("items", []),
            adapter=adapter,
            reply_target=reply_jid,
            messages=group_msgs,
            property_slug=property_slug,
            resolve_reporter=_resolve_reporter,
            sender_trusted=sender_trusted,
        )
        totals["replied"] += r.get("replied", 0)
        totals["logged"] += r.get("logged", 0)
        totals["errors"].extend(r.get("errors", []))

    return totals
