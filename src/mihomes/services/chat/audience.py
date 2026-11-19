"""Who can read a conversation — the inverse of `access.visible_conversation_ids` (SPEC-013 §5.3).

Used to narrow what the bot may say in a room (D7) and whom a post may @mention (D14). It must
agree with `visible_conversation_ids` for every (member, conversation) pair; A17 checks exactly
that, so a change to one without the other fails the suite rather than leaking.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.authz.scope import scoped_property_ids
from mihomes.models.chat_native import (
    KIND_DM,
    KIND_ESTATE,
    KIND_PROPERTY,
    ChatConversation,
    ChatParticipant,
)
from mihomes.models.membership import Membership, MembershipPropertyScope
from mihomes.models.user import User

__all__ = ["Audience", "audience", "display_name", "members_who_can_see"]

_PRIVILEGED = frozenset({"owner", "admin"})


@dataclass(frozen=True)
class Audience:
    has_staff: bool
    scope: frozenset[uuid.UUID] | None   # every member's scope intersected; None with no staff


def members_who_can_see(session: Session, conversation: ChatConversation) -> list[Membership]:
    """Active memberships for which `conversation.id` is in `visible_conversation_ids(m)`."""
    active = select(Membership).where(Membership.account_id == conversation.account_id,
                                      Membership.status == "active")
    if conversation.kind == KIND_ESTATE:
        query = active
    elif conversation.kind == KIND_PROPERTY:
        scoped = select(MembershipPropertyScope.membership_id).where(
            MembershipPropertyScope.property_id == conversation.property_id,
            MembershipPropertyScope.account_id == conversation.account_id)
        query = active.where(Membership.role.in_(_PRIVILEGED) | Membership.id.in_(scoped))
    elif conversation.kind == KIND_DM:
        query = active.where(Membership.id.in_(
            select(ChatParticipant.membership_id)
            .where(ChatParticipant.conversation_id == conversation.id)))
    else:  # pragma: no cover - the CHECK constraint forbids it
        return []
    return list(session.execute(query.order_by(Membership.created_at)).scalars())


def audience(session: Session, conversation: ChatConversation) -> Audience:
    members = members_who_can_see(session, conversation)
    if all(m.role in _PRIVILEGED for m in members):
        return Audience(False, None)
    scopes = [scoped_property_ids(session, m) for m in members]
    return Audience(True, frozenset.intersection(*scopes))


def display_name(session: Session, membership: Membership) -> str:
    """`users.name`, else the email's local part. Always rendered autoescaped."""
    user = session.get(User, membership.user_id)
    if user is None:
        return "Someone"
    return user.name or user.email.split("@")[0]
