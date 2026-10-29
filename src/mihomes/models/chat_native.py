"""Native in-app chat (SPEC-012). Seven tenant tables, all `ACCOUNT_SHARED`: RLS isolates accounts,
and `services.chat.access` alone decides which conversations a member sees (SPEC-012 §0.1).

`chat_messages` (models/chat_message.py) is the separate Telegram log and is untouched (D1).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned

KIND_ESTATE, KIND_PROPERTY, KIND_DM = "estate", "property", "dm"
AUTHOR_MEMBER, AUTHOR_BOT = "member", "bot"
MAX_MEMBER_BODY = 4000


def _uuid_pk():
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)


def _fk(target: str, *, nullable: bool, ondelete: str | None = "CASCADE"):
    return mapped_column(PGUUID(as_uuid=True), ForeignKey(target, ondelete=ondelete),
                         nullable=nullable, index=True)


def _created():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChatConversation(Base, TenantOwned):
    __tablename__ = "chat_conversations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    # The default satisfies ck_..._kind for the isolation seeder (§0.6); services always pass kind.
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default=KIND_ESTATE)
    # CASCADE, not SET NULL (the CHECK forbids a property chat with no property) and not RESTRICT
    # (deleting a property would fail for every property whose chat was ever opened).
    property_id: Mapped[uuid.UUID | None] = _fk("properties.id", nullable=True)
    # str(uuid_a) + ":" + str(uuid_b), sorted lexicographically: 36 + 1 + 36.
    dm_key: Mapped[str | None] = mapped_column(String(73), nullable=True)
    # Display override only. NULL = derived at render ("General", Property.name, the other member).
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    next_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        CheckConstraint("kind IN ('estate', 'property', 'dm')", name="ck_chat_conversations_kind"),
        CheckConstraint("(kind = 'property') = (property_id IS NOT NULL)",
                        name="ck_chat_conversations_property"),
        CheckConstraint("(kind = 'dm') = (dm_key IS NOT NULL)", name="ck_chat_conversations_dm_key"),
        Index("uq_chat_conversations_estate", "account_id", unique=True,
              postgresql_where=text("kind = 'estate'")),
        Index("uq_chat_conversations_property", "account_id", "property_id", unique=True,
              postgresql_where=text("kind = 'property'")),
        Index("uq_chat_conversations_dm", "account_id", "dm_key", unique=True,
              postgresql_where=text("kind = 'dm'")),
    )


class ChatParticipant(Base, TenantOwned):
    """DM membership (two rows per DM) **and** the read cursor for every kind (lazy for groups).

    Visibility reads it for `kind='dm'` only (D3): group read cursors live here too, so an
    unfiltered join would let a staff member keep a property chat after losing that property.
    """

    __tablename__ = "chat_participants"
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        primary_key=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"), primary_key=True,
        index=True)
    last_read_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0,
                                               server_default="0")
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                        server_default="false")
    joined_at: Mapped[datetime] = _created()


class ChatPost(Base, TenantOwned):
    __tablename__ = "chat_posts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = _fk("chat_conversations.id", nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # NULL means the bot. NO ACTION on delete: SET NULL would silently turn a person into the bot.
    author_membership_id: Mapped[uuid.UUID | None] = _fk("memberships.id", nullable=True,
                                                         ondelete=None)
    # Fail-loud default (§0.6): a member post that omits author_kind violates ck_chat_posts_author.
    author_kind: Mapped[str] = mapped_column(String(10), nullable=False, default=AUTHOR_BOT)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reply_to_id: Mapped[uuid.UUID | None] = _fk("chat_posts.id", nullable=True, ondelete="SET NULL")
    # Composer idempotency key (SPEC-013 P4): a double-submit carries the same token and returns the
    # first post instead of creating a second. NULL for bot posts and older clients.
    client_token: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    # SPEC-013 search. Deferred, so no ordinary load pays for it.
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', coalesce(body, ''))", persisted=True),
        deferred=True)

    __table_args__ = (
        CheckConstraint("author_kind IN ('member', 'bot')", name="ck_chat_posts_author_kind"),
        CheckConstraint("(author_kind = 'bot') = (author_membership_id IS NULL)",
                        name="ck_chat_posts_author"),
        CheckConstraint(f"author_kind = 'bot' OR char_length(body) <= {MAX_MEMBER_BODY}",
                        name="ck_chat_posts_body_len"),
        CheckConstraint("deleted_at IS NULL OR body = ''", name="ck_chat_posts_deleted_blank"),
        UniqueConstraint("conversation_id", "seq", name="uq_chat_posts_seq"),  # also history index
        Index("ix_chat_posts_search_tsv", "search_tsv", postgresql_using="gin"),
        Index("uq_chat_posts_client_token", "conversation_id", "client_token", unique=True,
              postgresql_where=text("client_token IS NOT NULL")),
    )


class ChatReaction(Base, TenantOwned):
    __tablename__ = "chat_reactions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    post_id: Mapped[uuid.UUID] = _fk("chat_posts.id", nullable=False)
    membership_id: Mapped[uuid.UUID] = _fk("memberships.id", nullable=False)
    emoji: Mapped[str] = mapped_column(String(16), nullable=False)   # allowlist in the service (D11)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (UniqueConstraint("post_id", "membership_id", "emoji", name="uq_chat_reactions"),)


class ChatAttachment(Base, TenantOwned):
    """Created here, written by SPEC-013. A row exists only with its post (same POST)."""

    __tablename__ = "chat_attachments"
    id: Mapped[uuid.UUID] = _uuid_pk()
    post_id: Mapped[uuid.UUID] = _fk("chat_posts.id", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (CheckConstraint("size_bytes >= 0", name="ck_chat_attachments_size"),)


class ChatMention(Base, TenantOwned):
    """Created here, written by SPEC-013, read by SPEC-014 (`notified_at`)."""

    __tablename__ = "chat_mentions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    post_id: Mapped[uuid.UUID] = _fk("chat_posts.id", nullable=False)
    membership_id: Mapped[uuid.UUID] = _fk("memberships.id", nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()

    __table_args__ = (UniqueConstraint("post_id", "membership_id", name="uq_chat_mentions"),)


class ChatEvent(Base, TenantOwned):
    """The SSE catch-up log; `cursor` is the SSE `id:`. UUID PK (SPEC-002 D2) plus an identity
    column, because Last-Event-ID needs a total order a UUIDv7 does not promise within 1 ms."""

    __tablename__ = "chat_events"
    id: Mapped[uuid.UUID] = _uuid_pk()
    cursor: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False,
                                        unique=True)
    conversation_id: Mapped[uuid.UUID] = _fk("chat_conversations.id", nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)   # events.EVENT_TYPES, no CHECK
    post_id: Mapped[uuid.UUID | None] = _fk("chat_posts.id", nullable=True)
    actor_membership_id: Mapped[uuid.UUID | None] = _fk("memberships.id", nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now(), index=True)
