"""Turning chat posts into what the templates draw (SPEC-012 D12, D18).

**The tokenizer never produces markup.** `tokenize_body` splits a body into `text` and `link`
tokens; the template autoescapes every token's text and builds the `<a>` itself, so `|safe` is never
applied to anything a member typed. Only `http(s)://` becomes a link — `javascript:` and friends
stay text. SPEC-013 adds a `mention` token here.

`post_views` loads, in a fixed number of queries, everything a page of posts needs: author names,
reaction counts, reply previews, and whether the viewer may edit or delete each post.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.chat_native import AUTHOR_BOT, KIND_DM, ChatConversation, ChatPost, ChatReaction
from mihomes.models.membership import Membership
from mihomes.models.user import User

__all__ = ["BOT_NAME", "PostView", "Token", "post_views", "tokenize_body"]

BOT_NAME = "MiHomes"
_LINK = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}'\""
_SNIPPET = 80


@dataclass(frozen=True)
class Token:
    kind: str           # "text" | "link"
    text: str
    href: str | None = None


def tokenize_body(body: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    for match in _LINK.finditer(body):
        url = match.group(0)
        stripped = url.rstrip(_TRAILING)
        # Keep a closing paren that belongs to the URL, e.g. a Wikipedia link.
        while stripped.count("(") > stripped.count(")") and len(stripped) < len(url) \
                and url[len(stripped)] == ")":
            stripped += ")"
        if match.start() > pos:
            tokens.append(Token("text", body[pos:match.start()]))
        tokens.append(Token("link", stripped, stripped))
        pos = match.start() + len(stripped)
    if pos < len(body):
        tokens.append(Token("text", body[pos:]))
    return tokens


@dataclass
class ReactionCount:
    emoji: str
    count: int
    mine: bool


@dataclass
class PostView:
    post: ChatPost
    author_name: str
    is_bot: bool
    is_mine: bool
    can_edit: bool
    can_delete: bool
    tokens: list[Token]
    reactions: list[ReactionCount] = field(default_factory=list)
    reply_author: str | None = None
    reply_snippet: str | None = None   # None + reply_author set → the parent was deleted
    attachments: list = field(default_factory=list)   # ChatAttachment rows (SPEC-013)

    @property
    def id(self) -> uuid.UUID:
        return self.post.id

    @property
    def deleted(self) -> bool:
        return self.post.deleted_at is not None


def _names(session: Session, membership_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    ids = {m for m in membership_ids if m is not None}
    if not ids:
        return {}
    rows = session.execute(
        select(Membership.id, User.name, User.email)
        .join(User, User.id == Membership.user_id)
        .where(Membership.id.in_(ids))
    ).all()
    return {r.id: (r.name or r.email.split("@")[0]) for r in rows}


def _snippet(body: str) -> str:
    flat = " ".join(body.split())
    return flat if len(flat) <= _SNIPPET else flat[:_SNIPPET - 1] + "…"


def post_views(session: Session, viewer: Membership, conversation: ChatConversation,
               posts: list[ChatPost]) -> list[PostView]:
    if not posts:
        return []
    post_ids = [p.id for p in posts]
    parent_ids = {p.reply_to_id for p in posts if p.reply_to_id}
    parents = {p.id: p for p in session.execute(
        select(ChatPost).where(ChatPost.id.in_(parent_ids))).scalars()} if parent_ids else {}
    names = _names(session, [p.author_membership_id for p in posts]
                   + [p.author_membership_id for p in parents.values()])

    counts: dict[uuid.UUID, dict[str, list]] = defaultdict(dict)
    for post_id, emoji, membership_id in session.execute(
        select(ChatReaction.post_id, ChatReaction.emoji, ChatReaction.membership_id)
        .where(ChatReaction.post_id.in_(post_ids))
        .order_by(ChatReaction.created_at)
    ).all():
        entry = counts[post_id].setdefault(emoji, [0, False])
        entry[0] += 1
        entry[1] = entry[1] or membership_id == viewer.id

    moderator = (viewer.role in ("owner", "admin") and viewer.status == "active"
                 and conversation.kind != KIND_DM)
    from mihomes.services.chat.attachments import attachments_for

    files = attachments_for(session, post_ids)
    views = []
    for p in posts:
        is_bot = p.author_kind == AUTHOR_BOT
        is_mine = not is_bot and p.author_membership_id == viewer.id
        live = p.deleted_at is None
        view = PostView(
            post=p,
            author_name=BOT_NAME if is_bot else names.get(p.author_membership_id, "Former member"),
            is_bot=is_bot,
            is_mine=is_mine,
            can_edit=live and is_mine,
            can_delete=live and (is_mine or moderator),
            tokens=tokenize_body(p.body) if live else [],
            reactions=[ReactionCount(e, n, mine) for e, (n, mine) in counts[p.id].items()],
            attachments=files.get(p.id, []) if live else [],
        )
        parent = parents.get(p.reply_to_id) if p.reply_to_id else None
        if parent is not None:
            view.reply_author = (BOT_NAME if parent.author_kind == AUTHOR_BOT
                                 else names.get(parent.author_membership_id, "Former member"))
            view.reply_snippet = _snippet(parent.body) if parent.deleted_at is None else None
        views.append(view)
    return views
