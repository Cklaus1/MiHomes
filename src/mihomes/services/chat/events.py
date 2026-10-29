"""Chat events: the catch-up log plus after-commit live publish (SPEC-012 D8, D16, D17).

Every chat mutation calls `emit`, which inserts a `ChatEvent` (its `cursor` is the SSE `id:`) and
queues `(account_id, payload)` on the session. **Only on commit** does `_after_commit` hand the queue
to the bus; a rollback discards it, so a rolled-back post is never announced.

Payloads carry **ids only** (D8): the client re-fetches the post partial, which re-checks visibility.
A leaked event therefore reveals an id, never content.

The account travels *with* the event, taken from the row — never re-read from a ContextVar at commit
time: SPEC-013's background dispatch and the NOTIFY listener have no request context.

With `REALTIME_DATABASE_URL` set (D17), `emit` also runs `pg_notify` inside the transaction —
Postgres delivers a NOTIFY only on commit, which is exactly after-commit semantics — and the
listener task (bus.start_listener) does the local publish instead of `_after_commit`.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, event, text
from sqlalchemy.orm import Session

from mihomes.models.chat_native import ChatEvent

__all__ = ["EVENT_TYPES", "NOTIFY_CHANNEL", "RETENTION", "emit", "notify_mode", "prune_events"]

EVENT_TYPES = frozenset({
    "post.created", "post.edited", "post.deleted", "reaction.changed",
    "read.changed", "conversation.created",
})  # typing is never stored (D15)

RETENTION = timedelta(days=7)
NOTIFY_CHANNEL = "mihomes_chat"
_QUEUE_KEY = "chat_events"


def notify_mode() -> bool:
    """True when cross-process fan-out via LISTEN/NOTIFY is configured (D17)."""
    return bool(os.environ.get("REALTIME_DATABASE_URL", "").strip())


def emit(session: Session, *, conversation_id: uuid.UUID, type: str,  # noqa: A002 - spec name
         post_id: uuid.UUID | None = None,
         actor_membership_id: uuid.UUID | None = None) -> ChatEvent:
    """Record one event and queue it for publish-on-commit. Unknown type → ValueError."""
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown chat event type {type!r}")
    row = ChatEvent(conversation_id=conversation_id, type=type, post_id=post_id,
                    actor_membership_id=actor_membership_id)
    session.add(row)
    session.flush()  # assigns the identity `cursor`, which the SSE frame needs
    payload = {
        "id": row.cursor,
        "type": type,
        "c": str(conversation_id),
        "p": str(post_id) if post_id else None,
        "m": str(actor_membership_id) if actor_membership_id else None,
    }
    if notify_mode():
        session.execute(text("SELECT pg_notify(:ch, :body)"),
                        {"ch": NOTIFY_CHANNEL,
                         "body": json.dumps({**payload, "a": str(row.account_id)})})
    else:
        session.info.setdefault(_QUEUE_KEY, []).append((row.account_id, payload))
    return row


def _after_commit(session: Session) -> None:
    """Hand the committed events to the bus. Never runs SQL (the transaction is already over)."""
    queued = session.info.pop(_QUEUE_KEY, None)
    if not queued:
        return
    from mihomes.services.chat.bus import BUS

    for account_id, payload in queued:
        BUS.publish(account_id, payload)


def _after_rollback(session: Session) -> None:
    """A rolled-back event is never announced."""
    session.info.pop(_QUEUE_KEY, None)


def install_listeners() -> None:
    """Idempotent; bound to the Session class as tenancy/connection.py does."""
    if not event.contains(Session, "after_commit", _after_commit):
        event.listen(Session, "after_commit", _after_commit)
    if not event.contains(Session, "after_soft_rollback", _after_soft_rollback):
        event.listen(Session, "after_soft_rollback", _after_soft_rollback)


def _after_soft_rollback(session: Session, previous_transaction) -> None:
    # Only the outermost rollback discards the queue: a SAVEPOINT rollback (e.g. a failed side
    # write) must not drop events from work that will still commit.
    if previous_transaction.parent is None:
        _after_rollback(session)


def prune_events(session: Session, *, now: datetime | None = None) -> int:
    """Delete this account's events older than RETENTION. Returns the number removed."""
    cutoff = (now or datetime.now(timezone.utc)) - RETENTION
    return session.execute(delete(ChatEvent).where(ChatEvent.created_at < cutoff)).rowcount or 0


install_listeners()
