"""ChatMessage — the stored log of a bound group chat, shown on the Conversations page (2026-10-27).

Written by `services/gateways/conversation_log.py` from `review_common.handle_inbound`, so every
channel (Telegram webhook or poller, WhatsApp) records the same way. Owners and admins read it at
`/conversations`; staff never do (account-level, like `TelegramLink`).

**Group conversations only.** A person's private chat with the bot is not stored here. Neither is a
`/link CODE` message (a link code is a bearer credential) or a media file path — `has_media` marks
that a photo was sent, nothing more.

`message_id` is the envelope `id` for inbound messages (stable across redelivery, so the unique
constraint makes recording idempotent) and a generated id for the bot's replies.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned

INBOUND = "in"
OUTBOUND = "out"


class ChatMessage(Base, TenantOwned):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    has_media: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    property_slug: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "account_id", "channel", "chat_id", "message_id", name="uq_chat_messages_message"
        ),
        Index("ix_chat_messages_conversation", "account_id", "channel", "chat_id", "sent_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ChatMessage {self.channel}:{self.chat_id} {self.direction}>"
