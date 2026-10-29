"""Chat search (SPEC-013 D15, D16).

Postgres full text over the generated `chat_posts.search_tsv` ('simple' config, GIN). Only the
searcher's visible conversations and only live posts; never an `IN ()` and never an unfiltered
query. `websearch_to_tsquery` cannot fail on user syntax. Results are newest first with a keyset
cursor, so paging stays stable while new posts arrive. Highlights come back wrapped in the
renderer's sentinel characters — never HTML — and are drawn by `render.tokenize_body`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, literal_column, select, tuple_
from sqlalchemy.orm import Session

from mihomes.models.chat_native import ChatPost
from mihomes.models.membership import Membership
from mihomes.services.chat.access import visible_conversation_ids
from mihomes.services.chat.render import HIT_START, HIT_STOP

__all__ = ["MAX_QUERY_CHARS", "PAGE_SIZE", "SearchHit", "SearchPage", "search_posts"]

PAGE_SIZE = 20
MAX_QUERY_CHARS = 200
_SIMPLE = literal_column("'simple'::regconfig")


@dataclass(frozen=True)
class SearchHit:
    post_id: uuid.UUID
    conversation_id: uuid.UUID
    seq: int
    created_at: datetime
    author_membership_id: uuid.UUID | None
    snippet: str


@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]
    next_cursor: str | None          # "<created_at iso>|<post uuid>"


def _decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    at, pid = cursor.split("|", 1)
    return datetime.fromisoformat(at), uuid.UUID(pid)


def search_posts(session: Session, membership: Membership, q: str, *,
                 cursor: str | None = None, limit: int = PAGE_SIZE) -> SearchPage:
    q = (q or "").strip()[:MAX_QUERY_CHARS]
    visible = visible_conversation_ids(session, membership)
    if not q or not visible:
        return SearchPage([], None)
    tsq = func.websearch_to_tsquery(_SIMPLE, q)
    snippet = func.ts_headline(
        _SIMPLE, ChatPost.body, tsq,
        f"StartSel={HIT_START}, StopSel={HIT_STOP}, MaxWords=30, MinWords=10, MaxFragments=2")
    stmt = (select(ChatPost.id, ChatPost.conversation_id, ChatPost.seq, ChatPost.created_at,
                   ChatPost.author_membership_id, snippet.label("snippet"))
            .where(ChatPost.conversation_id.in_(list(visible)),
                   ChatPost.deleted_at.is_(None),
                   ChatPost.search_tsv.op("@@")(tsq))
            .order_by(ChatPost.created_at.desc(), ChatPost.id.desc())
            .limit(limit + 1))
    if cursor:
        try:
            at, pid = _decode(cursor)
        except ValueError:
            at = pid = None
        if at is not None:
            stmt = stmt.where(tuple_(ChatPost.created_at, ChatPost.id) < tuple_(at, pid))
    rows = session.execute(stmt).all()
    hits = [SearchHit(r.id, r.conversation_id, r.seq, r.created_at, r.author_membership_id,
                      r.snippet) for r in rows[:limit]]
    next_cursor = (f"{hits[-1].created_at.isoformat()}|{hits[-1].post_id}"
                   if len(rows) > limit else None)
    return SearchPage(hits, next_cursor)
