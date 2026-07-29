"""Telegram responder — logs issues/tasks and answers questions via AI.

Thin gateway shim over `services.gateways.review_common`: this module supplies
only the Telegram-specific pieces (client construction, chat-links, the
inventory-scan route, and approver identification by Telegram user_id). All the
message analysis, category dispatch, photo-attach, and AI reply logic lives in
the shared core so it stays identical to the WhatsApp gateway.
"""

import base64
import json
import logging
import mimetypes
import os

from sqlalchemy.orm import Session

from mihomes.services.gateways import review_common as rc
from mihomes.services.gateways.telegram.client import TelegramClient, TelegramError
from mihomes.services.gateways.telegram.review import analyze_messages

logger = logging.getLogger("mihomes.telegram")


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
                notes=item.get("note"),
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


def _is_approver(session: Session, message: dict) -> bool:
    """True when a message is from the configured Telegram approver (by user_id)."""
    from mihomes.services.config_service import get_config
    approver_id = get_config(session, "telegram.pto_approver_id")
    if not approver_id:
        return False
    return str(message.get("sender", "")) == str(approver_id).strip()


def process_and_respond(
    session: Session,
    messages: list[dict],
    property_slug: str | None = None,
) -> dict:
    """Analyze Telegram messages, create issues/tasks, answer questions, handle PTO.

    Delegates all analysis and dispatch to `review_common`; only the client,
    inventory route, and approver identity are Telegram-specific.

    Returns dict with: logged, replied, errors.
    """
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    from mihomes.services.ai.provider import AIProviderError
    client = _get_client(session)

    adapter = rc.GatewayAdapter(
        label="Telegram",
        send=lambda chat_id, text: client.send_message(chat_id, text),
    )

    # Pre-check: handle APPROVE/DENY from the approver before AI analysis (H24).
    messages = rc.handle_approval_messages(
        session, messages,
        adapter=adapter,
        is_approver=lambda m: _is_approver(session, m),
    )
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    # M26: a batch can span several chats of the same property. Dispatch each
    # chat independently so replies land in the chat they came from.
    groups = rc.group_by_target(
        [m for m in messages if m.get("propertySlug") or property_slug]
    )
    if not groups:
        return {"replied": 0, "logged": 0, "errors": ["No linked chat found"]}

    from mihomes.services.config_service import get_config
    inventory_chat_id = get_config(session, "telegram.inventory_chat_id")

    # Telegram has no phone numbers — reporter is matched by name only.
    def _resolve_reporter(item: dict) -> int | None:
        return rc.resolve_reporter_by_name(session, item.get("reported_by"))

    totals = {"replied": 0, "logged": 0, "errors": []}
    for reply_chat_id, chat_msgs in groups.items():
        # Route inventory chat straight to room scanner
        if inventory_chat_id and reply_chat_id == str(inventory_chat_id):
            inv_property = (chat_msgs[0].get("propertySlug") or property_slug or "belle-estate")
            r = handle_inventory_scan(session, chat_msgs, inv_property, reply_chat_id, client)
            totals["replied"] += r.get("replied", 0)
            totals["logged"] += r.get("logged", 0)
            totals["errors"].extend(r.get("errors", []))
            continue

        def _send_error_to_group(detail: str, _cid=reply_chat_id) -> None:
            # M27: generic message to the group; detail is logged locally only.
            try:
                client.send_message(_cid, "Bot error — couldn't process message(s). Please log manually.")
            except Exception:
                logger.exception("_send_error_to_group: suppressed exception")
            logger.error("responder group error (chat %s): %s", _cid, detail)

        try:
            result = analyze_messages(session, chat_msgs, property_name=property_slug, property_slug=property_slug)
        except AIProviderError as e:
            logger.error("AI provider error: %s", e)
            _send_error_to_group(str(e))
            totals["errors"].append(f"AI provider error: {e}")
            continue
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            _send_error_to_group(str(e))
            totals["errors"].append(f"Unexpected error: {e}")
            continue

        # M27: sensitive actions only for a group whose every sender is trusted.
        sender_trusted = bool(chat_msgs) and all(
            rc.is_trusted_sender(session, m, gateway="telegram") for m in chat_msgs
        )

        r = rc.dispatch_items(
            session, result.get("items", []),
            adapter=adapter,
            reply_target=reply_chat_id,
            messages=chat_msgs,
            property_slug=property_slug,
            resolve_reporter=_resolve_reporter,
            sender_trusted=sender_trusted,
        )
        totals["replied"] += r.get("replied", 0)
        totals["logged"] += r.get("logged", 0)
        totals["errors"].extend(r.get("errors", []))

    return totals
