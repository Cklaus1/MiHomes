"""@mentions (SPEC-013 D14).

The body stays plain text (`@Maria Lopez`) — no stored `<@uuid>` tokens to pollute search or the
edit box. The server re-resolves every mention against the people who can see the conversation;
the composer's `mention_ids` are only a hint for telling two people with the same name apart. A
forged hint for someone outside the room is dropped, so a mention can never notify a person who
cannot read the post it points at.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.chat_native import ChatConversation, ChatMention, ChatPost
from mihomes.models.membership import Membership
from mihomes.services.chat.audience import display_name, members_who_can_see

__all__ = ["MENTIONABLE_LIMIT", "Mentionable", "mention_names_for", "mentionable",
           "record_mentions", "resolve_mentions"]

MENTIONABLE_LIMIT = 8


@dataclass(frozen=True)
class Mentionable:
    membership_id: uuid.UUID
    name: str


def _named(session: Session, members: list[Membership]) -> list[tuple[Membership, str]]:
    return [(m, display_name(session, m)) for m in members]


def mentionable(session: Session, conversation: ChatConversation, *, q: str,
                exclude: uuid.UUID) -> list[Mentionable]:
    prefix = (q or "").strip().lower()
    out = [Mentionable(m.id, name) for m, name in
           _named(session, members_who_can_see(session, conversation))
           if m.id != exclude and name.lower().startswith(prefix)]
    return sorted(out, key=lambda x: x.name.lower())[:MENTIONABLE_LIMIT]


def _mentioned(body: str, name: str) -> bool:
    return re.search(rf"(?<!\w)@{re.escape(name)}(?!\w)", body, re.IGNORECASE) is not None


def resolve_mentions(session: Session, conversation: ChatConversation, body: str,
                     hinted_ids: Iterable[uuid.UUID]) -> list[Membership]:
    """A candidate is mentioned iff `@<name>` appears at a word boundary AND (it was hinted OR its
    name is unique among the people who can see the conversation)."""
    hinted = {uuid.UUID(str(h)) for h in hinted_ids}
    candidates = _named(session, members_who_can_see(session, conversation))
    counts: dict[str, int] = {}
    for _m, name in candidates:
        counts[name.lower()] = counts.get(name.lower(), 0) + 1
    return [m for m, name in candidates
            if _mentioned(body or "", name) and (m.id in hinted or counts[name.lower()] == 1)]


def record_mentions(session: Session, post: ChatPost, members: list[Membership]) -> list[ChatMention]:
    """One row per distinct member, author excluded; `notified_at` stays NULL for SPEC-014."""
    seen: set[uuid.UUID] = set()
    rows = []
    for m in members:
        if m.id == post.author_membership_id or m.id in seen:
            continue
        seen.add(m.id)
        rows.append(ChatMention(account_id=post.account_id, post_id=post.id, membership_id=m.id))
    session.add_all(rows)
    session.flush()
    return rows


def mention_names_for(session: Session, post_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """post id → the display names it mentions (one query plus name lookups)."""
    out: dict[uuid.UUID, list[str]] = {}
    if not post_ids:
        return out
    rows = session.execute(
        select(ChatMention.post_id, Membership)
        .join(Membership, Membership.id == ChatMention.membership_id)
        .where(ChatMention.post_id.in_(post_ids))).all()
    for post_id, member in rows:
        out.setdefault(post_id, []).append(display_name(session, member))
    return out
