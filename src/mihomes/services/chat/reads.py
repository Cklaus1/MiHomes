"""Read cursors and unread counts (SPEC-012 D13).

Unread is per viewer: non-deleted posts with `seq > last_read_seq`, never your own. A group the
member has no participant row in yet counts only posts created after they joined the account, so a
new hire does not open the app to thousands of unread. Read receipts ("Seen") are shown in DMs only
— in a group, "seen by 7 of 9" is presence by another name, and presence is out of scope.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from mihomes.models.chat_native import KIND_DM, ChatConversation, ChatParticipant, ChatPost
from mihomes.models.membership import Membership

__all__ = ["last_read_seq", "mark_read", "total_unread", "unread_counts"]


def last_read_seq(session: Session, membership_id: uuid.UUID, conversation_id: uuid.UUID) -> int:
    return session.execute(
        select(ChatParticipant.last_read_seq).where(
            ChatParticipant.conversation_id == conversation_id,
            ChatParticipant.membership_id == membership_id)
    ).scalar() or 0


def mark_read(session: Session, membership: Membership, conversation_id: uuid.UUID,
              seq: int, *, announce: bool = True) -> bool:
    """Advance the cursor to `seq` (never backwards). True if it advanced.

    `read.changed` goes out only for a **DM** (D13: "Seen" is a DM-only signal; in a group it
    would be "seen by 7 of 9", presence by another name), and only when `announce` — an author's
    cursor moving past their own new post is not news to anyone.
    """
    before = last_read_seq(session, membership.id, conversation_id)
    if seq <= before:
        return False
    stmt = pg_insert(ChatParticipant.__table__).values(
        account_id=membership.account_id, conversation_id=conversation_id,
        membership_id=membership.id, last_read_seq=seq,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["conversation_id", "membership_id"],
        set_={"last_read_seq": func.greatest(ChatParticipant.__table__.c.last_read_seq,
                                             stmt.excluded.last_read_seq)},
    )
    session.execute(stmt)
    if announce:
        kind = session.execute(select(ChatConversation.kind)
                               .where(ChatConversation.id == conversation_id)).scalar()
        if kind == KIND_DM:
            from mihomes.services.chat.events import emit

            emit(session, conversation_id=conversation_id, type="read.changed",
                 actor_membership_id=membership.id)
    return True


def unread_counts(session: Session, membership: Membership,
                  conversation_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
    """One grouped query over the given conversations (callers pass the visible set)."""
    ids = list(conversation_ids)
    if not ids:
        return {}
    participant = ChatParticipant.__table__.alias("p")
    rows = session.execute(
        select(ChatPost.conversation_id, func.count(ChatPost.id))
        .select_from(ChatPost)
        .outerjoin(participant, and_(participant.c.conversation_id == ChatPost.conversation_id,
                                     participant.c.membership_id == membership.id))
        .where(
            ChatPost.conversation_id.in_(ids),
            ChatPost.deleted_at.is_(None),
            ChatPost.author_membership_id.is_distinct_from(membership.id),
            # A cursor if there is one; otherwise only posts since the member joined (D13).
            func.coalesce(
                ChatPost.seq > participant.c.last_read_seq,
                ChatPost.created_at > membership.created_at,
            ),
        )
        .group_by(ChatPost.conversation_id)
    ).all()
    return {conversation_id: count for conversation_id, count in rows}


def total_unread(session: Session, membership: Membership) -> int:
    """The sidebar badge: total over this member's visible conversations only."""
    from mihomes.services.chat.access import visible_conversation_ids

    return sum(unread_counts(session, membership,
                             visible_conversation_ids(session, membership)).values())
