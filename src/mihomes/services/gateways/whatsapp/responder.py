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
from mihomes.services.gateways.answer_scope import answer_scope_for
from mihomes.services.gateways.whatsapp.client import WhatsAppClient
from mihomes.services.gateways.whatsapp.review import analyze_messages

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

    # L2: with several properties and no label on the message, we can't know
    # which estate the assets belong to. Ask rather than misfile them.
    if not property_slug:
        client.send_group_message(
            reply_jid,
            "🏠 Which property is this? Set the inventory group's property with "
            "`mihomes whatsapp monitor --property <slug>` so scanned items file correctly.",
        )
        return {"replied": 1, "logged": 0, "errors": ["No property for inventory scan"]}

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




def _reporter_for(session: Session, convo: list[dict]):
    """Match the reporter by the sender's phone (H25: from `sender`), then by AI-extracted name."""
    from mihomes.models.staff import Staff

    phone_to_staff: dict[str, object] = {}
    for msg in convo:
        phone = rc.sender_phone(msg)
        if phone and phone not in phone_to_staff:
            for s in session.query(Staff).filter(Staff.whatsapp_phone.isnot(None)).all():
                s_phone = (s.whatsapp_phone or "").replace("+", "").replace("-", "").replace(" ", "")
                if s_phone and (s_phone in phone or phone in s_phone):
                    phone_to_staff[phone] = s
                    break

    def _resolve_reporter(item: dict) -> int | None:
        for msg in convo:
            phone = rc.sender_phone(msg)
            if phone and phone in phone_to_staff:
                return phone_to_staff[phone].id
        return rc.resolve_reporter_by_name(session, item.get("reported_by"))

    return _resolve_reporter


def process_and_respond(
    session: Session,
    messages: list[dict],
    property_slug: str | None = None,
) -> dict:
    """Analyze messages, create issues/tasks, answer questions, handle PTO.

    Builds WhatsApp's `ChannelAdapter` and hands off to `review_common.handle_inbound`; only the
    client, "🏠 " reply prefix, inventory route, approver identity and phone-based reporter
    matching are WhatsApp-specific.

    Returns dict with: logged, replied, errors.
    """
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    from mihomes.services.config_service import get_config
    client = WhatsAppClient()

    adapter = rc.GatewayAdapter(
        label="WhatsApp",
        channel="whatsapp",
        # WhatsApp prefixes every outbound message with the house emoji.
        send=lambda jid, text: client.send_group_message(jid, f"🏠 {text}"),
        error_text=f"⚠️ {rc.DEFAULT_ERROR_TEXT}",
        is_approver=lambda m: _is_approver(session, m),
        reporter_for=_reporter_for,
        inventory_target=get_config(session, "whatsapp.inventory_group_jid"),
        scan_inventory=lambda s, msgs, prop, target: handle_inventory_scan(s, msgs, prop, target, client),
        analyze=lambda *a, **k: analyze_messages(*a, **k),
        answer_scope=answer_scope_for("whatsapp"),
    )
    return rc.handle_inbound(session, messages, adapter=adapter, property_slug=property_slug)
