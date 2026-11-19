"""Reactions on chat posts (SPEC-012 O1).

A fixed set, so a reaction is one of eight buttons, never free text. Rows holding an emoji later
dropped from the set still render; they just can't be added again. Nothing here commits.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mihomes.models.chat_native import ChatPost, ChatReaction
from mihomes.models.membership import Membership
from mihomes.services.chat.access import ChatInvalid
from mihomes.services.chat.events import emit

__all__ = ["EMOJI_ALLOWLIST", "toggle_reaction"]

EMOJI_ALLOWLIST: tuple[str, ...] = ("👍", "❤️", "😂", "😮", "😢", "🙏", "✅", "👀")   # O1


def toggle_reaction(session: Session, post: ChatPost, *, actor: Membership, emoji: str) -> bool:
    """Add `actor`'s `emoji` to `post`, or remove it if present. True if it is now present."""
    if emoji not in EMOJI_ALLOWLIST:
        raise ChatInvalid("That reaction isn't available.")
    if post.deleted_at is not None:
        raise ChatInvalid("That message was deleted.")
    existing = session.execute(
        select(ChatReaction.id).where(ChatReaction.post_id == post.id,
                                      ChatReaction.membership_id == actor.id,
                                      ChatReaction.emoji == emoji)
    ).scalar_one_or_none()
    if existing is not None:
        session.execute(delete(ChatReaction).where(ChatReaction.id == existing))
    else:
        session.add(ChatReaction(account_id=actor.account_id, post_id=post.id,
                                 membership_id=actor.id, emoji=emoji))
        session.flush()
    emit(session, conversation_id=post.conversation_id, type="reaction.changed", post_id=post.id,
         actor_membership_id=actor.id)
    return existing is None
