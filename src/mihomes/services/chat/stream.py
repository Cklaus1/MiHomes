"""The chat event stream (SPEC-012 §5.4, D8, D9, D15).

One `EventSource` per open chat tab. The generator holds **no** DB connection: every read is a
short-lived session in a worker thread (N5), opened inside `account_context` with the tenant GUC
bound explicitly — a bare session on the app role sees no rows, and an empty visible set would make
"never delivers an invisible event" pass by delivering nothing at all.

Ordering of the first two steps matters: subscribe **before** the catch-up read, so an event
committed between the two is either replayed or queued, never lost. Duplicates are expected (the
catch-up overlap, the subscribe race) and share an SSE `id:`; the client dedupes.

Events carry ids only (D8). The filter is the visible set, refreshed on `conversation.created` and
every `REFRESH_S`; a session that stops resolving, or a membership that stops being active, ends the
stream with `event: revoked`.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from typing import AsyncIterator, Callable, ContextManager

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from mihomes.models.chat_native import ChatEvent
from mihomes.models.membership import Membership
from mihomes.services.chat.access import visible_conversation_ids
from mihomes.services.chat.bus import BUS, ChatBus

__all__ = ["CATCHUP_LIMIT", "HEARTBEAT_S", "MAX_LIFETIME_S", "REFRESH_S", "stream_events"]

# Module attributes, read at call time, so tests can shrink them.
HEARTBEAT_S = 20.0
REFRESH_S = 60.0
MAX_LIFETIME_S = 15 * 60.0   # then the browser reconnects and the request gate runs again
CATCHUP_LIMIT = 500
CATCHUP_OVERLAP = timedelta(seconds=5)


def _default_open_session() -> ContextManager[Session]:
    from mihomes.db import get_session

    return get_session()


@contextmanager
def _bound(open_session, account_id: uuid.UUID, user_id: uuid.UUID):
    from mihomes.tenancy.connection import bind_account_guc
    from mihomes.tenancy.context import account_context

    with account_context(account_id, user_id):
        with open_session() as session:
            bind_account_guc(session, account_id)
            yield session


def frame(event: dict) -> str:
    """One SSE frame. Typing has no `id:` — it is not replayable and must not move the cursor."""
    lines = []
    if event.get("id") is not None and event.get("type") != "typing":
        lines.append(f"id: {event['id']}")
    lines.append(f"event: {event['type']}")
    lines.append("data: " + json.dumps(event, default=str, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


async def stream_events(*, account_id: uuid.UUID, user_id: uuid.UUID, membership_id: uuid.UUID,
                        session_cookie: str | None, after_cursor: int | None,
                        open_session: Callable[[], ContextManager[Session]] = _default_open_session,
                        bus: ChatBus = BUS) -> AsyncIterator[str]:
    sub = bus.subscribe(account_id)

    def still_allowed() -> set[str] | None:
        """The visible set as strings, or None if this stream must end."""
        from mihomes.auth.sessions import lookup_session

        with _bound(open_session, account_id, user_id) as s:
            if session_cookie is not None:
                auth = lookup_session(s, session_cookie)   # read-only; never touches last_seen_at
                if auth is None or auth.account_id != account_id or auth.user_id != user_id:
                    return None
                from mihomes.tenancy.connection import bind_account_guc

                bind_account_guc(s, account_id)   # lookup_session re-binds the user GUC
            m = s.get(Membership, membership_id)
            if m is None or m.status != "active" or m.account_id != account_id:
                return None
            return {str(c) for c in visible_conversation_ids(s, m)}

    def catch_up(visible: set[str]) -> list[dict] | None:
        """Missed events after `after_cursor`, or None when the gap is too big to replay."""
        if after_cursor is None or not visible:
            return []
        with _bound(open_session, account_id, user_id) as s:
            ids = [uuid.UUID(c) for c in visible]
            anchor = s.execute(select(ChatEvent.created_at)
                               .where(ChatEvent.cursor == after_cursor)).scalar()
            if anchor is None:
                newest = s.execute(select(func.max(ChatEvent.cursor))
                                   .where(ChatEvent.conversation_id.in_(ids))).scalar() or 0
                # The anchor was pruned (or never ours): replay is impossible unless nothing is newer.
                return [] if newest <= after_cursor else None
            # Identity values are assigned at insert and committed out of order, so a slow
            # transaction can commit a *lower* cursor after a higher one was seen. The overlap
            # re-sends a few seconds' worth; the client drops ids it already has.
            rows = s.execute(
                select(ChatEvent)
                .where(ChatEvent.conversation_id.in_(ids),
                       or_(ChatEvent.cursor > after_cursor,
                           ChatEvent.created_at >= anchor - CATCHUP_OVERLAP))
                .order_by(ChatEvent.cursor)
                .limit(CATCHUP_LIMIT + 1)
            ).scalars().all()
            if len(rows) > CATCHUP_LIMIT:
                return None
            return [{"id": r.cursor, "type": r.type, "c": str(r.conversation_id),
                     "p": str(r.post_id) if r.post_id else None,
                     "m": str(r.actor_membership_id) if r.actor_membership_id else None}
                    for r in rows if r.cursor != after_cursor]

    try:
        visible = await run_in_threadpool(still_allowed)
        if visible is None:
            yield frame({"type": "revoked"})
            return
        missed = await run_in_threadpool(catch_up, visible)
        if missed is None:
            yield frame({"type": "resync"})
            return
        for event in missed:
            yield frame(event)

        started = last_refresh = time.monotonic()
        while True:
            now = time.monotonic()
            if now - started >= MAX_LIFETIME_S:
                return
            if now - last_refresh >= REFRESH_S:
                visible = await run_in_threadpool(still_allowed)
                last_refresh = time.monotonic()
                if visible is None:
                    yield frame({"type": "revoked"})
                    return
            wait = min(HEARTBEAT_S, REFRESH_S - (now - last_refresh),
                       MAX_LIFETIME_S - (now - started))
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=max(wait, 0.01))
            except asyncio.TimeoutError:
                yield ": hb\n\n"
                continue
            if event.get("type") == "resync":
                yield frame(event)
                return
            if event.get("type") == "conversation.created":
                # Refresh first: a new DM's own creation event is outside the stale set.
                visible = await run_in_threadpool(still_allowed)
                last_refresh = time.monotonic()
                if visible is None:
                    yield frame({"type": "revoked"})
                    return
            if str(event.get("c")) not in visible:
                continue
            if event.get("type") == "typing" and str(event.get("m")) == str(membership_id):
                continue   # your own typing is not news to you
            yield frame(event)
    finally:
        bus.unsubscribe(sub)
