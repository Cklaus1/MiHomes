"""Conversation lifecycle and the visible list (SPEC-012 D6).

Group conversations (the estate "General" and one per property) are created lazily on first open of
`/chat/`; a DM is created the first time one member opens it with another. All three use
`INSERT … ON CONFLICT DO NOTHING` against the partial unique index, then SELECT — race-free, unlike
an IntegrityError retry loop. A `conversation.created` event is emitted only by the insert that won.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from mihomes.authz.scope import scoped_property_ids
from mihomes.ids import new_id
from mihomes.models.chat_native import (
    KIND_DM,
    KIND_ESTATE,
    KIND_PROPERTY,
    ChatConversation,
    ChatParticipant,
)
from mihomes.models.membership import Membership
from mihomes.models.property import Property
from mihomes.models.user import User
from mihomes.services.chat.access import ChatInvalid, visible_conversation_ids

__all__ = [
    "ConversationRow",
    "dm_candidates",
    "dm_key_for",
    "ensure_group_conversations",
    "get_or_create_dm",
    "get_or_create_estate",
    "get_or_create_property",
    "list_for",
    "title_for",
]

ESTATE_TITLE = "General"


def _account_id() -> uuid.UUID:
    from mihomes.tenancy.context import current_account

    return current_account.get()


def _insert_once(session: Session, *, kind: str, index_elements: list[str],
                 **values) -> tuple[ChatConversation, bool]:
    """Insert-if-absent against the partial unique index for `kind`; returns (row, created)."""
    account_id = _account_id()
    row = {"id": new_id(), "account_id": account_id, "kind": kind, **values}
    stmt = (
        pg_insert(ChatConversation.__table__)
        .values(**row)
        .on_conflict_do_nothing(index_elements=index_elements,
                                index_where=text(f"kind = '{kind}'"))
    )
    created = session.execute(stmt).rowcount == 1
    query = select(ChatConversation).where(ChatConversation.account_id == account_id,
                                           ChatConversation.kind == kind)
    for col, value in values.items():
        query = query.where(getattr(ChatConversation, col) == value)
    conversation = session.execute(query).scalar_one()
    if created:
        from mihomes.services.chat.events import emit

        emit(session, conversation_id=conversation.id, type="conversation.created")
    return conversation, created


def get_or_create_estate(session: Session) -> ChatConversation:
    return _insert_once(session, kind=KIND_ESTATE, index_elements=["account_id"])[0]


def get_or_create_property(session: Session, property_id: uuid.UUID) -> ChatConversation:
    return _insert_once(session, kind=KIND_PROPERTY, index_elements=["account_id", "property_id"],
                        property_id=property_id)[0]


def dm_key_for(a: uuid.UUID, b: uuid.UUID) -> str:
    return ":".join(sorted([str(a), str(b)]))


def get_or_create_dm(session: Session, me: Membership,
                     other_membership_id: uuid.UUID) -> ChatConversation:
    """The DM between `me` and another active member of this account. ChatInvalid for yourself or a
    non-active (or foreign) membership. Both participant rows are ensured."""
    if other_membership_id == me.id:
        raise ChatInvalid("You can't start a conversation with yourself.")
    other = session.get(Membership, other_membership_id)
    if other is None or other.status != "active" or other.account_id != me.account_id:
        raise ChatInvalid("That person isn't an active member of this estate.")

    conversation, _created = _insert_once(
        session, kind=KIND_DM, index_elements=["account_id", "dm_key"],
        dm_key=dm_key_for(me.id, other.id),
    )
    for membership_id in (me.id, other.id):
        session.execute(
            pg_insert(ChatParticipant.__table__)
            .values(account_id=me.account_id, conversation_id=conversation.id,
                    membership_id=membership_id)
            .on_conflict_do_nothing(index_elements=["conversation_id", "membership_id"])
        )
    return conversation


def ensure_group_conversations(session: Session, membership: Membership) -> None:
    """D6: the estate chat plus one chat per property this member can see. Idempotent."""
    if membership.status != "active":
        return
    get_or_create_estate(session)
    for property_id in scoped_property_ids(session, membership):
        get_or_create_property(session, property_id)


@dataclass(frozen=True)
class ConversationRow:
    id: uuid.UUID
    kind: str
    title: str
    unread: int
    last_message_at: datetime | None
    property_id: uuid.UUID | None = None


def _other_member_names(session: Session, me: Membership,
                        dm_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """conversation_id -> the other participant's display name, for these DMs only."""
    if not dm_ids:
        return {}
    rows = session.execute(
        select(ChatParticipant.conversation_id, User.name, User.email)
        .join(Membership, Membership.id == ChatParticipant.membership_id)
        .join(User, User.id == Membership.user_id)
        .where(ChatParticipant.conversation_id.in_(dm_ids),
               ChatParticipant.membership_id != me.id)
    ).all()
    return {r.conversation_id: (r.name or r.email) for r in rows}


def title_for(session: Session, me: Membership, conversation: ChatConversation) -> str:
    if conversation.title:
        return conversation.title
    if conversation.kind == KIND_ESTATE:
        return ESTATE_TITLE
    if conversation.kind == KIND_PROPERTY:
        prop = session.get(Property, conversation.property_id)
        return prop.name if prop else "Property"
    return _other_member_names(session, me, [conversation.id]).get(conversation.id, "Direct message")


def list_for(session: Session, membership: Membership) -> list[ConversationRow]:
    """Visible conversations only: General, properties A–Z, then DMs by recency.

    Titles are derived for **visible ids only** — on a chat route a staff `select(Property)` is
    unfiltered (§0.1), so the property names must come from the visible set, never a bare query.
    """
    from mihomes.services.chat.reads import unread_counts

    ids = visible_conversation_ids(session, membership)
    if not ids:
        return []
    conversations = session.execute(
        select(ChatConversation).where(ChatConversation.id.in_(ids))
    ).scalars().all()
    unread = unread_counts(session, membership, ids)

    prop_ids = [c.property_id for c in conversations if c.kind == KIND_PROPERTY]
    prop_names = dict(session.execute(
        select(Property.id, Property.name).where(Property.id.in_(prop_ids))
    ).all()) if prop_ids else {}
    dm_names = _other_member_names(session, membership,
                                   [c.id for c in conversations if c.kind == KIND_DM])

    def row(c: ChatConversation) -> ConversationRow:
        title = (c.title or (ESTATE_TITLE if c.kind == KIND_ESTATE else
                 prop_names.get(c.property_id, "Property") if c.kind == KIND_PROPERTY else
                 dm_names.get(c.id, "Direct message")))
        return ConversationRow(c.id, c.kind, title, unread.get(c.id, 0), c.last_message_at,
                               c.property_id)

    rows = [row(c) for c in conversations]
    estate = [r for r in rows if r.kind == KIND_ESTATE]
    props = sorted((r for r in rows if r.kind == KIND_PROPERTY), key=lambda r: r.title.lower())
    # Most recent first; a DM with no messages yet sorts last.
    dms = sorted((r for r in rows if r.kind == KIND_DM),
                 key=lambda r: (r.last_message_at is not None,
                                r.last_message_at.timestamp() if r.last_message_at else 0.0),
                 reverse=True)
    return estate + props + dms


def dm_candidates(session: Session, membership: Membership):
    """Active members of this account other than me (`MemberView`s); render `display_name` only."""
    from mihomes.services.membership_service import list_members

    return [m for m in list_members(session, membership.account_id)
            if m.membership_id != membership.id]
