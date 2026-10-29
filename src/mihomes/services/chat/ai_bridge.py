"""The AI in group chats — native chat as one more gateway channel (SPEC-013 §5.2).

A group post (General or a property chat, never a DM — D1) is turned into an `InternalMessage`
and run through the same `review_common.handle_inbound` as Telegram, with a `GatewayAdapter` whose
`send` posts as the bot into the source conversation only (D2). Everything else — the fourteen
dispatch branches, sensitive-action trust, `AccountMismatch` — is shared.

**Three things differ from Telegram, each because a chat room's membership is known:**

* **The property is chosen first** (D4/D5). A property chat uses its own; General uses the
  author's only scoped property, or one they named; otherwise the bot asks — but only if the post
  was actionable, and never naming a property.
* **Answers are narrowed to the room** (D7). If any reader is staff, money questions are refused
  and every answer runs as staff scoped to the property every reader can see.
* **Every item is pinned to the chat's property** (A13): the model cannot file into a house the
  room isn't about.

**Bot replies commit on their own session.** `create_post` takes the conversation's row lock
(SPEC-012 D7); holding it in the dispatch session would block every human post in the room while
the next 70–110 s AI call (the issue's expert note) ran. So `send` opens a short session, posts,
commits. The cost, the same as Telegram's: a crash after "logged ✓" was posted can still roll the
issue back. The dispatch session itself never writes chat rows, so the two never wait on each other.
"""

from __future__ import annotations

import logging
import mimetypes
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.authz.scope import authz_context, scoped_property_ids
from mihomes.models.chat_native import (
    AUTHOR_MEMBER,
    KIND_ESTATE,
    KIND_PROPERTY,
    ChatAttachment,
    ChatConversation,
    ChatPost,
)
from mihomes.models.membership import Membership
from mihomes.models.property import Property
from mihomes.models.staff import Staff
from mihomes.services.chat.audience import audience, display_name
from mihomes.services.chat.bus import BUS
from mihomes.services.gateways import review_common
from mihomes.services.gateways.message import InternalMessage
from mihomes.services.gateways.telegram.financial_guard import STAFF_REFUSAL, is_financial_question

logger = logging.getLogger(__name__)

__all__ = [
    "AI_KINDS", "ASK_WHICH_PROPERTY", "CHANNEL", "FINANCE_IN_SHARED_ROOM", "OUTSIDE_AUDIENCE",
    "PROCESSED_IDS_KEY", "PropertyRoute", "build_messages", "dispatch", "mihomes_adapter",
    "resolve_chat_property", "run", "should_dispatch",
]

CHANNEL = "mihomes"
PROCESSED_IDS_KEY = "mihomes.processed_ids"
MAX_PROCESSED_IDS = 5000
AI_KINDS = frozenset({KIND_ESTATE, KIND_PROPERTY})   # D1: never "dm"
_PRIVILEGED = frozenset({"owner", "admin"})

#: Full-UUID ids only (what `notify_approver` tells approvers to type). Every owner/admin is an
#: approver here, so `handle_approval_messages`' loose `APPROVE\s+(\S+)` would turn "Approve the
#: paint quote" into approve_pto("the") + "Could not approve #the" (A29).
APPROVAL_COMMAND = re.compile(
    r"^\s*(APPROVE|DENY)\s+[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)

ASK_WHICH_PROPERTY = ("Which property is this about? Post it in that property's chat, "
                      "or include the property's name.")
FINANCE_IN_SHARED_ROOM = ("That's a financial question, and this chat includes people who "
                          "can't see finances. Ask it on the Ask AI page instead.")
OUTSIDE_AUDIENCE = ("I can't answer that here: not everyone in this chat can see that "
                    "property. Ask in that property's chat.")


@dataclass(frozen=True)
class PropertyRoute:
    slug: str | None
    property_id: uuid.UUID | None


def should_dispatch(conversation: ChatConversation, post: ChatPost) -> bool:
    return (conversation.kind in AI_KINDS and post.author_kind == AUTHOR_MEMBER
            and post.deleted_at is None)


def resolve_chat_property(session: Session, conversation: ChatConversation,
                          author: Membership, text: str) -> PropertyRoute:
    """D4. Property chat → its property. General → the author's sole scoped property, else the
    single scoped property named in `text` (whole word: name or slug), else none."""
    if conversation.kind == KIND_PROPERTY:
        prop = session.get(Property, conversation.property_id)
        return PropertyRoute(prop.slug, prop.id) if prop else PropertyRoute(None, None)
    allowed = scoped_property_ids(session, author)
    props = (session.execute(select(Property).where(Property.id.in_(allowed))).scalars().all()
             if allowed else [])
    if len(props) == 1:
        return PropertyRoute(props[0].slug, props[0].id)
    lowered = (text or "").lower()
    hits = [p for p in props if any(
        re.search(rf"(?<!\w){re.escape(t.lower())}(?!\w)", lowered)
        for t in {p.name, p.slug, p.slug.replace("-", " ")} if t)]
    return PropertyRoute(hits[0].slug, hits[0].id) if len(hits) == 1 else PropertyRoute(None, None)


def build_messages(session: Session, post: ChatPost, *, conversation: ChatConversation,
                   author: Membership, property_slug: str | None,
                   media_dir: Path) -> list[InternalMessage]:
    """D3. One envelope for the text and first image, one more per extra image. PDFs become a
    ' [file: name]' suffix. Images are copied into `media_dir` (owned by `dispatch`) because
    `_load_image_attachments` reads a real path, and mimetypes decides image-ness by suffix."""
    from mihomes.storage import get_storage

    attachments = session.execute(
        select(ChatAttachment).where(ChatAttachment.post_id == post.id)
        .order_by(ChatAttachment.created_at)).scalars().all()
    images, files = [], []
    for att in attachments:
        if att.content_type.startswith("image/"):
            suffix = Path(att.storage_key).suffix or (mimetypes.guess_extension(att.content_type) or "")
            path = media_dir / f"{uuid.uuid4().hex}{suffix}"
            path.write_bytes(get_storage().get(att.storage_key))
            images.append(str(path))
        else:
            files.append(att.filename)
    text = (post.body or "") + "".join(f" [file: {name}]" for name in files)
    created = post.created_at.isoformat() if post.created_at else ""

    def envelope(msg_id: str, body: str, media: str | None) -> InternalMessage:
        return InternalMessage(
            id=msg_id, timestamp=created, channel=CHANNEL, jid=str(conversation.id),
            isGroup=True, sender=str(author.id), senderName=display_name(session, author),
            senderUsername="", text=body, hasMedia=media is not None, mediaPath=media,
            propertySlug=property_slug)

    messages = [envelope(str(post.id), text.strip(), images[0] if images else None)]
    messages += [envelope(f"{post.id}:{n}", "", path) for n, path in enumerate(images[1:], 1)]
    return messages


def _property_id(session: Session, property_slug: str | None) -> uuid.UUID | None:
    if not property_slug:
        return None
    return session.execute(select(Property.id).where(Property.slug == property_slug)).scalar()


def _answer_scope(conversation: ChatConversation, author: Membership):
    """D7/D8, with the hook signature `review_common` calls. Never yields `None` with staff in
    the room unless a staff restriction is bound — a bare `None` would answer unrestricted."""

    @contextmanager
    def scope(session: Session, text: str, property_slug: str | None, messages: list[dict], *,
              money_gate: bool):
        aud = audience(session, conversation)
        if not aud.has_staff:
            yield None          # owners/admins only: the room may see everything they may
            return
        if money_gate and is_financial_question(text):
            yield FINANCE_IN_SHARED_ROOM if author.role in _PRIVILEGED else STAFF_REFUSAL
            return
        prop_id = _property_id(session, property_slug)
        if prop_id is None or prop_id not in (aud.scope or frozenset()):
            yield OUTSIDE_AUDIENCE   # an expert note discards this: refusal there is silent (D8)
            return
        with authz_context("staff", frozenset({prop_id})):
            yield None

    return scope


def _reporter_for(session: Session, convo: list[dict]) -> Callable[[dict], int | None]:
    """The sender's own Staff row (via the membership's user), else a name match."""
    staff_id = None
    sender = next((m.get("sender") for m in convo if m.get("sender")), None)
    try:
        membership = session.get(Membership, uuid.UUID(str(sender))) if sender else None
    except ValueError:
        membership = None
    if membership is not None:
        staff_id = session.execute(
            select(Staff.id).where(Staff.user_id == membership.user_id)).scalars().first()

    def resolve(item: dict) -> int | None:
        if staff_id is not None:
            return staff_id
        return review_common.resolve_reporter_by_name(session, item.get("reported_by"))

    return resolve


def _post_as_bot(conversation_id: uuid.UUID, text: str) -> None:
    """A short session of its own, committed at once — see the module docstring."""
    from mihomes.db import get_session
    from mihomes.services.chat.posts import create_bot_post
    from mihomes.tenancy.connection import bind_account_guc
    from mihomes.tenancy.context import current_account

    with get_session() as s:
        bind_account_guc(s, current_account.get())
        create_bot_post(s, conversation_id, text)


def mihomes_adapter(session: Session, conversation: ChatConversation, *,
                    author: Membership, route: PropertyRoute) -> review_common.GatewayAdapter:
    def send(target: str, text: str) -> None:
        if target != str(conversation.id):
            raise ValueError("mihomes adapter replies only into the source conversation")
        _post_as_bot(conversation.id, text)

    def analyze(s, convo, *, property_name=None, property_slug=None):
        result = review_common.analyze_messages(s, convo, property_name=property_name,
                                                property_slug=property_slug)
        for item in result.get("items", []):
            item["property_slug"] = route.slug   # A13: no cross-property writes from a chat
        return result

    def is_approver(message: dict) -> bool:
        return (bool(APPROVAL_COMMAND.match(message.get("text") or ""))
                and review_common.trusted_membership(session, message.get("sender"),
                                                     author.account_id))

    return review_common.GatewayAdapter(
        label="MiHomes chat", channel=CHANNEL, send=send, is_approver=is_approver,
        reporter_for=_reporter_for, analyze=analyze,
        answer_scope=_answer_scope(conversation, author),
    )


def _ask_if_actionable(session: Session, messages: list[InternalMessage],
                       adapter: review_common.GatewayAdapter) -> dict:
    """D5: analyse with no property; ask only if something was actionable. Never dispatches."""
    target = messages[0]["jid"]
    try:
        result = review_common.analyze_messages(session, messages)
    except Exception as e:
        logger.error("chat ai: analysis failed: %s", e)
        adapter.send(target, adapter.error_text)
        return {"replied": 1, "logged": 0, "errors": [str(e)]}
    actionable = [i for i in result.get("items", []) if i.get("category") != "informational"]
    if not actionable:
        return {"replied": 0, "logged": 0, "errors": []}
    adapter.send(target, ASK_WHICH_PROPERTY)
    return {"replied": 1, "logged": 0, "errors": []}


def run(session: Session, post: ChatPost, *, conversation: ChatConversation,
        author: Membership, media_dir: Path) -> dict:
    route = resolve_chat_property(session, conversation, author, post.body)
    adapter = mihomes_adapter(session, conversation, author=author, route=route)
    messages = build_messages(session, post, conversation=conversation, author=author,
                              property_slug=route.slug, media_dir=media_dir)
    if route.slug is None and not APPROVAL_COMMAND.match(post.body or ""):
        return _ask_if_actionable(session, messages, adapter)
    return review_common.handle_inbound(session, messages, adapter=adapter,
                                        property_slug=route.slug)


def _signal(account_id, conversation_id, post_id, type_: str) -> None:
    """Transient (like typing): never a chat_events row, so a reconnect never replays a spinner."""
    BUS.publish(account_id, {"type": type_, "c": str(conversation_id), "p": str(post_id)})


def dispatch(account_id: uuid.UUID, conversation_id: uuid.UUID, post_id: uuid.UUID) -> None:
    """BackgroundTasks entry point. `ai.done` fires on every return path (A14)."""
    from mihomes.db import get_session
    from mihomes.services.gateways.dedup import ProcessedIdStore
    from mihomes.tenancy.connection import bind_account_guc
    from mihomes.tenancy.context import account_context

    try:
        with account_context(account_id):
            store = ProcessedIdStore(PROCESSED_IDS_KEY, cap=MAX_PROCESSED_IDS)
            if store.contains(str(post_id)):
                return
            store.add([str(post_id)])   # claim BEFORE processing: a crash is never retried
            with get_session() as session, \
                    tempfile.TemporaryDirectory(prefix="mihomes-chat-") as tmp:
                bind_account_guc(session, account_id)
                post = session.get(ChatPost, post_id)
                conversation = post and session.get(ChatConversation, post.conversation_id)
                if (conversation is None or conversation.id != conversation_id
                        or not should_dispatch(conversation, post)):
                    return
                author = session.get(Membership, post.author_membership_id)
                if author is None or author.status != "active":
                    return
                _signal(account_id, conversation_id, post_id, "ai.thinking")
                run(session, post, conversation=conversation, author=author, media_dir=Path(tmp))
    except Exception:
        logger.exception("chat ai: post %s failed", post_id)
    finally:
        _signal(account_id, conversation_id, post_id, "ai.done")


