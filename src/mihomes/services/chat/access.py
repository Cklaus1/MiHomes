"""Which conversations a member sees — **THE chat authorization boundary** (SPEC-012 D3).

`chat.use` admits every logged-in role, so on a chat route the query layer binds no property scope
and filters nothing (SPEC-012 §0.1): a staff member's `select(Property)` there returns every
property. Visibility is therefore this module and nothing else — every read path (list, page,
history, the post partial, the stream, the badge, and SPEC-013's search and attachments) calls
`visible_conversation_ids` / `require_visible` first.

The visible set:
* the estate conversation ("General") — every active member;
* property conversations whose property is in `scoped_property_ids` (owner/admin: all; staff:
  their scope rows; staff with none: none);
* DM conversations with a participant row for this member — **joined on kind = 'dm' only**.
  Group read cursors also live in `chat_participants`, so an unfiltered join would let a staff
  member keep a property chat after losing that property.

A non-active membership sees nothing. Owners and admins do not see other people's DMs (D4).
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from mihomes.authz.scope import scoped_property_ids
from mihomes.models.chat_native import (
    KIND_DM,
    KIND_ESTATE,
    KIND_PROPERTY,
    ChatConversation,
    ChatParticipant,
    ChatPost,
)
from mihomes.models.membership import Membership

__all__ = [
    "ChatForbidden",
    "ChatInvalid",
    "ChatNotFound",
    "post_for",
    "require_visible",
    "visible_conversation_ids",
]


class ChatNotFound(LookupError):
    """→ 404. "Not visible" and "does not exist" are the same answer, so ids are not probeable."""


class ChatForbidden(PermissionError):
    """→ 403. The conversation is visible, but this verb is refused (e.g. editing another's post)."""


class ChatInvalid(ValueError):
    """→ 400. Empty body, unknown emoji, cross-conversation reply, DM with yourself…"""


def visible_conversation_ids(session: Session, membership: Membership) -> set[uuid.UUID]:
    """The ids of every conversation `membership` may see. `set()` for a non-active membership."""
    if membership is None or membership.status != "active":
        return set()

    properties = scoped_property_ids(session, membership)
    dm_ids = select(ChatParticipant.conversation_id).where(
        ChatParticipant.membership_id == membership.id
    )
    conditions = [
        ChatConversation.kind == KIND_ESTATE,
        # The DM arm MUST be restricted to kind = 'dm' (A6) — see the module docstring.
        (ChatConversation.kind == KIND_DM) & ChatConversation.id.in_(dm_ids),
    ]
    if properties:
        conditions.append(
            (ChatConversation.kind == KIND_PROPERTY)
            & ChatConversation.property_id.in_(properties)
        )
    rows = session.execute(
        select(ChatConversation.id).where(
            ChatConversation.account_id == membership.account_id, or_(*conditions)
        )
    ).scalars()
    return set(rows)


def require_visible(session: Session, membership: Membership,
                    conversation_id: uuid.UUID) -> ChatConversation:
    """The conversation, or `ChatNotFound`. Every route taking a conversation id calls this first."""
    if conversation_id not in visible_conversation_ids(session, membership):
        raise ChatNotFound(str(conversation_id))
    conversation = session.get(ChatConversation, conversation_id)
    if conversation is None:  # pragma: no cover - visible implies it exists
        raise ChatNotFound(str(conversation_id))
    return conversation


def post_for(session: Session, membership: Membership, post_id: uuid.UUID) -> ChatPost:
    """Load the post, then `require_visible` on its conversation. `ChatNotFound` either way."""
    post = session.get(ChatPost, post_id)
    if post is None:
        raise ChatNotFound(str(post_id))
    require_visible(session, membership, post.conversation_id)
    return post
