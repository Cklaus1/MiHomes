"""Posting, editing, deleting and paging chat posts (SPEC-012 D7, D12, D19).

**Ordering (D7).** Each post takes the conversation's next `seq` with one
`UPDATE … SET next_seq = next_seq + 1 RETURNING`: gap-free and unique under concurrency, with no
sequence object per conversation. The row lock lasts until commit, so routes commit before they
render (D10) — never hold it across a slow call.

**Permissions (D12).** Edit: the author only; never a bot post or a deleted post. Delete: the author,
or an owner/admin in a *group* chat — not in a DM they are not part of (they cannot see it, D4).
Soft delete blanks the body, sets `deleted_at` and drops the reactions; it is idempotent.

Nothing here commits; callers do.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from mihomes.models.chat_native import (
    AUTHOR_BOT,
    AUTHOR_MEMBER,
    KIND_DM,
    MAX_MEMBER_BODY,
    ChatConversation,
    ChatParticipant,
    ChatPost,
    ChatReaction,
)
from mihomes.models.membership import Membership
from mihomes.services.chat.access import (
    ChatForbidden,
    ChatInvalid,
    visible_conversation_ids,
)
from mihomes.services.chat.events import emit
from mihomes.services.chat.reads import mark_read

__all__ = [
    "EDIT_WINDOW",
    "MAX_MEMBER_BODY",
    "PAGE_SIZE",
    "create_bot_post",
    "create_post",
    "delete_post",
    "edit_post",
    "history",
]

EDIT_WINDOW: timedelta | None = None   # O2 — unlimited for now
PAGE_SIZE = 50
_PRIVILEGED = frozenset({"owner", "admin"})


def _clean(body: str | None) -> str:
    return (body or "").strip()


def _next_seq(session: Session, conversation_id: uuid.UUID) -> int:
    return session.execute(
        update(ChatConversation)
        .where(ChatConversation.id == conversation_id)
        .values(next_seq=ChatConversation.next_seq + 1, last_message_at=func.now())
        .returning(ChatConversation.next_seq - 1)
    ).scalar_one()


def _dm_other_is_active(session: Session, conversation: ChatConversation,
                        author: Membership) -> bool:
    others = session.execute(
        select(Membership.status)
        .join(ChatParticipant, ChatParticipant.membership_id == Membership.id)
        .where(ChatParticipant.conversation_id == conversation.id, Membership.id != author.id)
    ).scalars().all()
    return bool(others) and all(status == "active" for status in others)


def create_post(session: Session, conversation: ChatConversation, *, author: Membership | None,
                body: str, reply_to_id: uuid.UUID | None = None, allow_empty: bool = False,
                client_token: uuid.UUID | None = None) -> ChatPost:
    """Post into `conversation`. `author=None` is the bot. See the module docstring."""
    text = _clean(body)
    if not text and not allow_empty:
        raise ChatInvalid("A message can't be empty.")
    if author is not None and len(text) > MAX_MEMBER_BODY:
        raise ChatInvalid(f"Messages are limited to {MAX_MEMBER_BODY} characters.")

    if client_token is not None:
        existing = session.execute(
            select(ChatPost).where(ChatPost.conversation_id == conversation.id,
                                   ChatPost.client_token == client_token)
        ).scalar_one_or_none()
        if existing is not None:
            return existing  # a double-submit: same post, no new seq, no event

    if author is not None:
        if conversation.id not in visible_conversation_ids(session, author):
            raise ChatInvalid("You can't post in that conversation.")  # defence in depth
        if conversation.kind == KIND_DM and not _dm_other_is_active(session, conversation, author):
            raise ChatInvalid("That person is no longer a member of this estate.")

    if reply_to_id is not None:
        parent = session.get(ChatPost, reply_to_id)
        if parent is None or parent.conversation_id != conversation.id:
            raise ChatInvalid("You can only reply to a message in the same conversation.")

    post = ChatPost(
        account_id=conversation.account_id,
        conversation_id=conversation.id,
        seq=_next_seq(session, conversation.id),
        author_membership_id=author.id if author else None,
        author_kind=AUTHOR_MEMBER if author else AUTHOR_BOT,
        body=text,
        reply_to_id=reply_to_id,
        client_token=client_token,
    )
    session.add(post)
    session.flush()
    if author is not None:
        mark_read(session, author, conversation.id, post.seq)  # your own post is never unread
    emit(session, conversation_id=conversation.id, type="post.created", post_id=post.id,
         actor_membership_id=author.id if author else None)
    return post


def create_bot_post(session: Session, conversation_id: uuid.UUID, body: str) -> ChatPost:
    """SPEC-013's seam: the AI's reply into a group chat. DMs are people-only."""
    conversation = session.get(ChatConversation, conversation_id)
    if conversation is None:
        raise ChatInvalid("No such conversation.")
    if conversation.kind == KIND_DM:
        raise ChatInvalid("The assistant does not post in direct messages.")
    return create_post(session, conversation, author=None, body=body)


def edit_post(session: Session, post: ChatPost, *, actor: Membership, body: str) -> ChatPost:
    if post.author_kind == AUTHOR_BOT or post.author_membership_id != actor.id:
        raise ChatForbidden("You can only edit your own messages.")
    if post.deleted_at is not None:
        raise ChatInvalid("That message was deleted.")
    if EDIT_WINDOW is not None and datetime.now(timezone.utc) - post.created_at > EDIT_WINDOW:
        raise ChatForbidden("That message can no longer be edited.")
    text = _clean(body)
    if not text:
        raise ChatInvalid("A message can't be empty.")
    if len(text) > MAX_MEMBER_BODY:
        raise ChatInvalid(f"Messages are limited to {MAX_MEMBER_BODY} characters.")
    if text != post.body:
        post.body = text
        post.edited_at = datetime.now(timezone.utc)
        session.flush()
        emit(session, conversation_id=post.conversation_id, type="post.edited", post_id=post.id,
             actor_membership_id=actor.id)
    return post


def delete_post(session: Session, post: ChatPost, *, actor: Membership) -> ChatPost:
    if post.deleted_at is not None:
        return post  # idempotent
    conversation = session.get(ChatConversation, post.conversation_id)
    is_author = post.author_membership_id == actor.id
    moderator = (actor.role in _PRIVILEGED and actor.status == "active"
                 and conversation is not None and conversation.kind != KIND_DM)
    if not (is_author or moderator):
        raise ChatForbidden("You can only delete your own messages.")
    session.execute(delete(ChatReaction).where(ChatReaction.post_id == post.id))
    post.body = ""
    post.deleted_at = datetime.now(timezone.utc)
    session.flush()
    emit(session, conversation_id=post.conversation_id, type="post.deleted", post_id=post.id,
         actor_membership_id=actor.id)
    return post


def history(session: Session, conversation_id: uuid.UUID, *, before_seq: int | None = None,
            limit: int = PAGE_SIZE) -> list[ChatPost]:
    """The latest `limit` posts (before `before_seq` if given), returned oldest-first.

    The caller must already hold `require_visible` for this conversation.
    """
    query = select(ChatPost).where(ChatPost.conversation_id == conversation_id)
    if before_seq is not None:
        query = query.where(ChatPost.seq < before_seq)
    newest = session.execute(query.order_by(ChatPost.seq.desc()).limit(limit)).scalars().all()
    return list(reversed(newest))
