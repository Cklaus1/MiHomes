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
from mihomes.services.gateways.answer_scope import answer_scope_for
from mihomes.services.gateways.telegram.client import TelegramClient, TelegramError
from mihomes.services.gateways.telegram.review import analyze_messages

logger = logging.getLogger("mihomes.telegram")


def _get_client(session: Session) -> TelegramClient:
    """The deployment's bot (`TELEGRAM_BOT_TOKEN`) when set, else the account's configured one.

    The environment token wins because the webhook delivers every account's traffic through one
    bot; replying with a different account-configured token would answer from a different bot.
    The config fallback keeps a single-estate local install (the poller) working unchanged.
    """
    from mihomes.services.config_service import get_config
    from mihomes.services.gateways.telegram.client import platform_bot_token
    token = platform_bot_token() or get_config(session, "telegram.bot_token")
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

    # L2: with several properties and no label on the message, we can't know
    # which estate the assets belong to. Ask rather than misfile them.
    if not property_slug:
        client.send_message(
            reply_chat_id,
            "Which property is this? Set the inventory chat's property with "
            "`mihomes telegram monitor --property <slug>` so scanned items file correctly.",
        )
        return {"replied": 1, "logged": 0, "errors": ["No property for inventory scan"]}

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

    Builds Telegram's `ChannelAdapter` and hands off to `review_common.handle_inbound`; only the
    client, inventory route and approver identity are Telegram-specific. Telegram has no phone
    numbers, so the reporter is matched by name (the adapter default).

    `analyze` is a lambda over this module's `analyze_messages` so it is looked up at call time —
    tests patch `telegram.responder.analyze_messages` and `_get_client` by module path.

    Returns dict with: logged, replied, errors.
    """
    if not messages:
        return {"replied": 0, "logged": 0, "errors": []}

    from mihomes.services.config_service import get_config
    client = _get_client(session)

    adapter = rc.GatewayAdapter(
        label="Telegram",
        channel="telegram",
        send=lambda chat_id, text: client.send_message(chat_id, text),
        is_approver=lambda m: _is_approver(session, m),
        inventory_target=get_config(session, "telegram.inventory_chat_id"),
        scan_inventory=lambda s, msgs, prop, target: handle_inventory_scan(s, msgs, prop, target, client),
        analyze=lambda *a, **k: analyze_messages(*a, **k),
        answer_scope=answer_scope_for("telegram"),
    )
    return rc.handle_inbound(session, messages, adapter=adapter, property_slug=property_slug)
