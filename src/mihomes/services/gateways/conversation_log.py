"""Record a group conversation for the Conversations page (2026-10-27).

Called from `review_common.handle_inbound`, so every channel records the same way. **Recording must
never change what the bot does**: each write runs in its own SAVEPOINT, a failure is logged and
swallowed, and a redelivered message is skipped by the unique constraint rather than duplicated.
The lesson is H27's — a side effect that rolls back the batch's real work is worse than no log.

What is NOT stored, deliberately:
* private (non-group) chats — a person's own conversation with the bot is not estate content;
* a `/link CODE` message — the code is a bearer credential (SPEC-006 U7);
* a media file path — only that media was sent (`has_media`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from mihomes.ids import new_id
from mihomes.models.chat_message import INBOUND, OUTBOUND, ChatMessage

logger = logging.getLogger(__name__)

_TEXT_LIMIT = 4000


def _is_link_command(text: str) -> bool:
    from mihomes.services.gateways.linking import parse_link_command

    return parse_link_command(text) is not None


def _account_id():
    """The bound account — a Core insert bypasses the ORM stamp, so it is set explicitly.

    Raises LookupError when unbound; `_insert` swallows it, so an unbound caller records nothing.
    """
    from mihomes.tenancy.context import current_account

    account = current_account.get()
    return getattr(account, "id", account)


def _insert(session: Session, row: dict) -> None:
    """One idempotent insert in its own SAVEPOINT; never raises."""
    try:
        row = {"id": new_id(), "account_id": _account_id(), **row}
        table = ChatMessage.__table__
        if session.get_bind().dialect.name == "postgresql":
            stmt = pg_insert(table).values(**row).on_conflict_do_nothing(
                constraint="uq_chat_messages_message"
            )
        else:  # pragma: no cover - SQLite dev installs
            stmt = table.insert().prefix_with("OR IGNORE").values(**row)
        with session.begin_nested():
            session.execute(stmt)
    except Exception:
        logger.exception("conversation log: could not record a %s message", row.get("direction"))


def _parse_time(value) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def record_inbound(session: Session, message: dict, *, property_slug: str | None = None) -> None:
    """Store one inbound group message (InternalMessage). Skips DMs and /link codes."""
    if not message.get("isGroup") or not message.get("jid") or not message.get("id"):
        return
    text = message.get("text") or ""
    if _is_link_command(text):
        return
    _insert(session, {
        "channel": message.get("channel") or "unknown",
        "chat_id": str(message["jid"]),
        "message_id": str(message["id"]),
        "direction": INBOUND,
        "sender_name": (message.get("senderName") or "")[:200],
        "text": text[:_TEXT_LIMIT],
        "has_media": bool(message.get("hasMedia")),
        "property_slug": message.get("propertySlug") or property_slug,
        "sent_at": _parse_time(message.get("timestamp")),
    })


def record_outbound(session: Session, *, channel: str, chat_id: str, text: str, label: str,
                    property_slug: str | None = None) -> None:
    """Store one reply the bot sent into a group."""
    _insert(session, {
        "channel": channel or "unknown",
        "chat_id": str(chat_id),
        "message_id": f"out:{new_id()}",
        "direction": OUTBOUND,
        "sender_name": f"MiHomes ({label})"[:200],
        "text": (text or "")[:_TEXT_LIMIT],
        "has_media": False,
        "property_slug": property_slug,
        "sent_at": datetime.now(timezone.utc),
    })


# --------------------------------------------------------------------------- #
# Reading — the Conversations page (scoped ORM reads in the viewer's account)
# --------------------------------------------------------------------------- #
def list_conversations(session: Session) -> list[dict]:
    """One entry per stored conversation, most recent first."""
    from sqlalchemy import func, select

    rows = session.execute(
        select(
            ChatMessage.channel,
            ChatMessage.chat_id,
            func.count(ChatMessage.id).label("count"),
            func.max(ChatMessage.sent_at).label("last_at"),
            func.max(ChatMessage.property_slug).label("property_slug"),
        )
        .group_by(ChatMessage.channel, ChatMessage.chat_id)
        .order_by(func.max(ChatMessage.sent_at).desc())
    ).all()
    return [row._asdict() for row in rows]


def conversation_messages(session: Session, channel: str, chat_id: str, limit: int = 200):
    """The latest `limit` messages of one conversation, oldest first (chat order)."""
    from sqlalchemy import select

    newest = session.execute(
        select(ChatMessage)
        .where(ChatMessage.channel == channel, ChatMessage.chat_id == chat_id)
        .order_by(ChatMessage.sent_at.desc())
        .limit(limit)
    ).scalars().all()
    return list(reversed(newest))
