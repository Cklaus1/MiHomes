"""Telling an away member that someone wrote to them (SPEC-014 §5.1).

**"Away" means unread ten minutes later (§0.1)** — no presence. A member is due a notification for
a post when (D1): it is a DM they are in or a mention of them; they have not read past it; it is
10 min – 48 h old; they did not write it; it is not deleted; they are active **and can still see
the conversation at send time**; and they have not muted it.

**The job has no principal, so visibility is re-derived per recipient at send time (§0.2).** A
mention can be written while someone can see a room and the job can run after they lost it; that
re-check (`_deliverable`) is what keeps a private message from reaching the wrong person.

**Two channels, separate markers (D2).** Push goes once per item (`chat_mentions.notified_at`, or
`chat_participants.last_notified_seq` for DMs). Email is a digest at most every DIGEST_INTERVAL,
covering items created after `chat_notify_prefs.digest_through` — the cutoff it covered, not the
time it was sent, so an item that ages past ten minutes just after a digest is not lost.

**Order (D11):** advisory lock per account → markers + digest enqueue + event prune → commit →
only then push. Email is exactly-once (the outbox row commits with its marker); push is
at-most-once; no transaction is held open across an HTTP call.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import and_, cast, delete, false, func, literal, null, select, union_all, update
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased

from mihomes.ids import new_id
from mihomes.models.account import Account
from mihomes.models.chat_native import (
    KIND_DM,
    ChatConversation,
    ChatEvent,
    ChatMention,
    ChatParticipant,
    ChatPost,
)
from mihomes.models.chat_notify import ChatNotifyPref
from mihomes.models.membership import Membership
from mihomes.models.user import User
from mihomes.services.chat.access import visible_conversation_ids

logger = logging.getLogger(__name__)

__all__ = ["DIGEST_INTERVAL", "NOTIFY_AFTER", "NOTIFY_HORIZON", "AccountPlan", "Due",
           "NotifyResult", "due_items", "plan_account", "run_account", "run_chat_notify"]

NOTIFY_AFTER = timedelta(minutes=10)
NOTIFY_HORIZON = timedelta(hours=48)       # no backlog blast on the first run after deploy
DIGEST_INTERVAL = timedelta(minutes=60)    # O2
EVENTS_RETENTION = timedelta(days=7)
DM_DIGEST_TITLE = "Direct messages"


@dataclass(frozen=True)
class Due:
    membership_id: uuid.UUID
    conversation_id: uuid.UUID
    post_id: uuid.UUID
    seq: int
    created_at: datetime
    reason: str                       # "dm" | "mention"
    pushed: bool                      # already past the push marker
    mention_id: uuid.UUID | None = None


def due_items(session: Session, *, now: datetime) -> list[Due]:
    """The SQL half of D1: (a)–(e) and the participant mute. Visibility and the prefs mute are
    `_deliverable`'s, because visibility is a Python function SPEC-012 owns."""
    lo, hi = now - NOTIFY_HORIZON, now - NOTIFY_AFTER
    P, C, Part, M = ChatPost, ChatConversation, ChatParticipant, ChatMention
    window = (P.deleted_at.is_(None), P.created_at > lo, P.created_at <= hi)

    dms = (
        select(Part.membership_id, P.conversation_id, P.id, P.seq, P.created_at,
               literal("dm").label("reason"), (P.seq <= Part.last_notified_seq).label("pushed"),
               cast(null(), PGUUID(as_uuid=True)).label("mention_id"))
        .join(C, C.id == P.conversation_id)
        .join(Part, Part.conversation_id == P.conversation_id)
        .where(C.kind == KIND_DM, *window,
               P.seq > Part.last_read_seq,
               P.author_membership_id.is_distinct_from(Part.membership_id),
               Part.muted.is_(False))
    )
    mp = aliased(ChatParticipant)          # group participant rows are lazy → outer join
    mentions = (
        select(M.membership_id, P.conversation_id, P.id, P.seq, P.created_at,
               literal("mention").label("reason"), M.notified_at.is_not(None).label("pushed"),
               M.id.label("mention_id"))
        .join(P, P.id == M.post_id)
        .outerjoin(mp, and_(mp.conversation_id == P.conversation_id,
                            mp.membership_id == M.membership_id))
        .where(*window,
               P.seq > func.coalesce(mp.last_read_seq, 0),
               P.author_membership_id.is_distinct_from(M.membership_id),
               func.coalesce(mp.muted, false()).is_(False))
    )
    rows = session.execute(union_all(dms, mentions)).all()
    # A mention inside a DM appears twice; keep one per (member, post), preferring the DM row,
    # whose marker is the seq — but keep the mention's reason and id so its marker moves too.
    by_key: dict[tuple, Due] = {}
    for r in sorted(rows, key=lambda r: r[5] != "dm"):
        key = (r[0], r[2])
        if key not in by_key:
            by_key[key] = Due(*r)
        elif r[5] == "mention":
            d = by_key[key]
            by_key[key] = Due(d.membership_id, d.conversation_id, d.post_id, d.seq, d.created_at,
                              "mention", d.pushed, r[7])
    return list(by_key.values())


def _deliverable(session: Session, items: list[Due], *, now: datetime
                 ) -> dict[uuid.UUID, tuple[Membership, ChatNotifyPref | None, list[Due]]]:
    """D1(f)/(g): active, can still see the conversation **now**, not muted by prefs.
    No cache across members or runs — a scope change between runs is what this catches."""
    grouped: dict[uuid.UUID, list[Due]] = defaultdict(list)
    for item in items:
        grouped[item.membership_id].append(item)
    out = {}
    for member_id, member_items in grouped.items():
        m = session.get(Membership, member_id)
        if m is None or m.status != "active":
            continue
        visible = visible_conversation_ids(session, m)
        kept = [i for i in member_items if i.conversation_id in visible]
        if not kept:
            continue
        prefs = session.execute(select(ChatNotifyPref).where(
            ChatNotifyPref.membership_id == member_id)).scalar_one_or_none()
        if prefs is not None and prefs.muted_until is not None and prefs.muted_until > now:
            continue
        out[member_id] = (m, prefs, kept)
    return out


@dataclass
class AccountPlan:
    pushes: list[tuple[uuid.UUID, dict]] = field(default_factory=list)
    digests: list[tuple[str, dict]] = field(default_factory=list)
    mention_ids: list[uuid.UUID] = field(default_factory=list)
    dm_marks: dict[tuple[uuid.UUID, uuid.UUID], int] = field(default_factory=dict)
    digest_marks: list[tuple[uuid.UUID, datetime]] = field(default_factory=list)


def _base_url() -> str:
    return os.environ.get("MIHOMES_BASE_URL", "https://app.mihomes.ai").rstrip("/")


def plan_account(session: Session, *, now: datetime) -> AccountPlan:
    """D2. **Reads only.** Every un-pushed item is marked even for a member with push off or no
    device, so a device added later gets no burst of old items."""
    from mihomes.services.chat.conversations import title_for
    from mihomes.services.chat.push import build_payload

    plan = AccountPlan()
    cutoff = now - NOTIFY_AFTER
    deliverable = _deliverable(session, due_items(session, now=now), now=now)
    account = None
    for member_id, (member, prefs, items) in deliverable.items():
        conversations = {c.id: c for c in session.execute(select(ChatConversation).where(
            ChatConversation.id.in_({i.conversation_id for i in items}))).scalars()}

        def title(conv: ChatConversation, member: Membership = member) -> str:
            return DM_DIGEST_TITLE if conv.kind == KIND_DM else title_for(session, member, conv)

        unpushed = [i for i in items if not i.pushed]
        for i in unpushed:
            if i.mention_id is not None:
                plan.mention_ids.append(i.mention_id)
            if i.reason == "dm" or conversations[i.conversation_id].kind == KIND_DM:
                key = (i.conversation_id, member_id)
                plan.dm_marks[key] = max(plan.dm_marks.get(key, 0), i.seq)
        if unpushed and (prefs is None or prefs.push):
            per_conv: dict[uuid.UUID, str] = {}
            for i in unpushed:
                if i.reason == "mention" or i.conversation_id not in per_conv:
                    per_conv[i.conversation_id] = i.reason
            for conv_id, reason in per_conv.items():
                conv = conversations[conv_id]
                plan.pushes.append((member_id, build_payload(conv, reason, title=title(conv))))

        through = prefs.digest_through if prefs is not None else None
        if prefs is not None and not prefs.email_digest:
            continue
        if through is not None and through + DIGEST_INTERVAL > cutoff:
            continue
        fresh = [i for i in items if through is None or i.created_at > through]
        if not fresh:
            continue
        user = session.get(User, member.user_id)
        if user is None or not user.email:
            continue
        counts: dict[str, int] = defaultdict(int)
        for i in fresh:
            counts[title(conversations[i.conversation_id])] += 1
        if account is None:
            account = session.get(Account, member.account_id)
        base = _base_url()
        plan.digests.append((user.email, {
            "estate": account.name if account else "your estate",
            "conversations": [{"title": t, "count": n} for t, n in sorted(counts.items())],
            "total": sum(counts.values()),
            "chat_url": f"{base}/chat/",
            "settings_url": f"{base}/settings/connections#chat-notifications",
        }))
        plan.digest_marks.append((member_id, cutoff))
    return plan


def run_account(session: Session, *, now: datetime, email) -> list[tuple[uuid.UUID, dict]]:
    """D11, up to the commit. Returns the pushes to send after the caller commits."""
    from mihomes.tenancy import require_account

    account_id = require_account()
    got = session.execute(select(func.pg_try_advisory_xact_lock(
        func.hashtext(f"chat-notify:{account_id}")))).scalar()
    if not got:
        return []                                   # another run holds this account
    plan = plan_account(session, now=now)
    if plan.mention_ids:
        session.execute(update(ChatMention).where(ChatMention.id.in_(plan.mention_ids))
                        .values(notified_at=now))
    for (conv, member), seq in plan.dm_marks.items():
        session.execute(update(ChatParticipant)
                        .where(ChatParticipant.conversation_id == conv,
                               ChatParticipant.membership_id == member,
                               ChatParticipant.last_notified_seq < seq)
                        .values(last_notified_seq=seq))
    for member, through in plan.digest_marks:
        stmt = pg_insert(ChatNotifyPref.__table__).values(
            id=new_id(), account_id=account_id, membership_id=member, digest_through=through)
        session.execute(stmt.on_conflict_do_update(
            index_elements=["account_id", "membership_id"],
            set_={"digest_through": through}))
    for address, ctx in plan.digests:
        email.send_chat_digest(address, **ctx)       # enqueue only (§0.3)
    session.execute(delete(ChatEvent).where(ChatEvent.created_at < now - EVENTS_RETENTION))
    return plan.pushes


@dataclass
class NotifyResult:
    accounts: int = 0
    pushes: int = 0
    pushes_sent: int = 0
    digests: int = 0
    errors: list[str] = field(default_factory=list)


def run_chat_notify(session_factory, *, push_sender, email_factory: Callable, now: datetime
                    ) -> NotifyResult:
    """D10. Account ids are read with no context (`accounts` has no RLS); then, per account,
    BOTH phases inside one `account_context` — a push session outside it would find no
    subscriptions under RLS and send nothing, while superuser tests stayed green."""
    from mihomes.services.chat.push import send_pushes
    from mihomes.tenancy.context import account_context

    result = NotifyResult()
    with session_factory() as s:
        account_ids = list(s.execute(select(Account.id)).scalars())
    for account_id in account_ids:
        try:
            with account_context(account_id):
                with session_factory() as s:
                    email = email_factory(s)
                    before = _outbox_count(s)
                    pushes = run_account(s, now=now, email=email)
                    result.digests += _outbox_count(s) - before
                    s.commit()
                result.pushes += len(pushes)
                if pushes:
                    with session_factory() as s:
                        result.pushes_sent += send_pushes(s, pushes, push_sender).sent
                        s.commit()
            result.accounts += 1
        except Exception as exc:
            logger.exception("chat-notify: account %s failed", account_id)
            result.errors.append(f"{account_id}: {exc}")
    return result


def _outbox_count(session: Session) -> int:
    from mihomes.models.email_outbox import EmailOutbox

    return session.execute(select(func.count()).select_from(EmailOutbox)).scalar() or 0
