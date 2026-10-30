"""In-app chat pages (SPEC-012 §5.5).

Every route declares `chat.use` on `ACCOUNT`, which every role holds (D2) — so the grant decides
nothing about *which* conversations a member reaches. That is `services.chat.access` alone (D3):
each route taking a `conversation_id` calls `require_visible`, each taking a `post_id` calls
`post_for`, and both answer 404 for anything outside the viewer's set (A27).

**Mutating routes commit before rendering (D10).** The commit releases the per-conversation row
lock `create_post` takes (D7) and fires the after-commit publish, both before the response leaves.
"""

from __future__ import annotations

import logging
import math
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mihomes.auth.sessions import SESSION_COOKIE
from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.chat_native import KIND_DM, ChatConversation, ChatEvent, ChatPost
from mihomes.models.membership import Membership
from mihomes.services.chat import ai_bridge, ratelimit, reads, streams
from mihomes.services.chat import attachments as attachment_svc
from mihomes.services.chat import conversations as conv
from mihomes.services.chat import mentions as mention_svc
from mihomes.services.chat import posts as post_svc
from mihomes.services.chat import reactions as reaction_svc
from mihomes.services.chat.access import (
    ChatInvalid,
    ChatNotFound,
    post_for,
    require_visible,
    visible_conversation_ids,
)
from mihomes.services.chat.bus import BUS
from mihomes.services.chat.render import post_views
from mihomes.services.chat.stream import stream_events
from mihomes.web.deps import RequestPrincipal, get_db, require_authenticated, templates
from mihomes.web.forms import store_validated_upload_sync

router = APIRouter()
logger = logging.getLogger(__name__)

TYPING_THROTTLE_S = 3.0
_last_typing: dict[tuple[uuid.UUID, uuid.UUID], float] = {}


def _me(db: Session, principal: RequestPrincipal) -> Membership:
    return db.get(Membership, principal.membership_id)


def _throttled(limiter, me: Membership) -> Response | None:
    """SPEC-014 D8: a 429 with Retry-After, plain text, **no partial and no HX-Trigger** — htmx
    does not swap a 4xx, and the composer clears only on success, so the typed text survives."""
    wait = limiter.hit(str(me.id))
    if wait is None:
        return None
    return PlainTextResponse("You're sending messages quickly — wait a few seconds.",
                             status_code=429, headers={"Retry-After": str(math.ceil(wait))})


def _render_post(request: Request, db: Session, me: Membership, post: ChatPost):
    conversation = db.get(ChatConversation, post.conversation_id)
    return templates.TemplateResponse(request, "partials/chat/_post.html", {
        "v": post_views(db, me, conversation, [post])[0],
        "conversation": conversation,
        "emojis": reaction_svc.EMOJI_ALLOWLIST,
    })


def _last_event_cursor(db: Session, visible: set[uuid.UUID]) -> int:
    """The newest event cursor in the viewer's conversations — the stream resumes after it."""
    if not visible:
        return 0
    return db.execute(select(func.max(ChatEvent.cursor))
                      .where(ChatEvent.conversation_id.in_(visible))).scalar() or 0


def _page_context(db: Session, me: Membership) -> dict:
    return {
        "page": "chat",
        "rows": conv.list_for(db, me),
        "dm_candidates": conv.dm_candidates(db, me),
    }


@router.get("/")
@declares("chat.use", Access.ACCOUNT)
def chat_index(request: Request, principal: RequestPrincipal = require_authenticated(),
               db: Session = Depends(get_db)):
    me = _me(db, principal)
    conv.ensure_group_conversations(db, me)
    db.commit()
    return templates.TemplateResponse(request, "chat/index.html", {
        **_page_context(db, me), "selected_id": None,
    })


@router.get("/c/{conversation_id}")
@declares("chat.use", Access.ACCOUNT)
def chat_conversation(request: Request, conversation_id: uuid.UUID, at: int | None = None,
                      principal: RequestPrincipal = require_authenticated(),
                      db: Session = Depends(get_db)):
    """`?at=<seq>` (a search hit) loads that post's neighbourhood instead of the latest page."""
    me = _me(db, principal)
    conversation = require_visible(db, me, conversation_id)
    posts = (post_svc.history_around(db, conversation.id, at) if at is not None
             else post_svc.history(db, conversation.id))
    return templates.TemplateResponse(request, "chat/conversation.html", {
        **_page_context(db, me),
        "selected_id": conversation.id,
        "conversation": conversation,
        "title": conv.title_for(db, me, conversation),
        "views": post_views(db, me, conversation, posts),
        "has_older": bool(posts) and posts[0].seq > 1,
        "oldest_seq": posts[0].seq if posts else None,
        "newest_seq": posts[-1].seq if posts else 0,
        "last_event": _last_event_cursor(db, visible_conversation_ids(db, me)),
        "emojis": reaction_svc.EMOJI_ALLOWLIST,
        "is_dm": conversation.kind == KIND_DM,
        "jump_seq": at,
    })


@router.get("/c/{conversation_id}/posts", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_older_posts(request: Request, conversation_id: uuid.UUID, before: int | None = None,
                     principal: RequestPrincipal = require_authenticated(),
                     db: Session = Depends(get_db)):
    """An older page, oldest-first, headed by its own "Load older" button when there is more."""
    me = _me(db, principal)
    conversation = require_visible(db, me, conversation_id)
    posts = post_svc.history(db, conversation.id, before_seq=before)
    return templates.TemplateResponse(request, "partials/chat/_post_list.html", {
        "views": post_views(db, me, conversation, posts),
        "conversation": conversation,
        "emojis": reaction_svc.EMOJI_ALLOWLIST,
        "has_older": bool(posts) and posts[0].seq > 1,
        "oldest_seq": posts[0].seq if posts else None,
    })


@router.get("/posts/{post_id}", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_post(request: Request, post_id: uuid.UUID,
              principal: RequestPrincipal = require_authenticated(),
              db: Session = Depends(get_db)):
    """One post — what chat.js re-fetches when an event names it (D8)."""
    me = _me(db, principal)
    return _render_post(request, db, me, post_for(db, me, post_id))


@router.post("/c/{conversation_id}/posts", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_create_post(request: Request, conversation_id: uuid.UUID,
                     background: BackgroundTasks,
                     body: str = Form(""), reply_to_id: str = Form(""),
                     client_token: str = Form(""),
                     mention_ids: list[str] = Form(default=[]),
                     files: list[UploadFile] = File(default=[]),
                     principal: RequestPrincipal = require_authenticated(),
                     db: Session = Depends(get_db)):
    """SPEC-013 §5.4 order: visibility first (nothing is stored for a non-member), then the files,
    then the post — and if anything after storing fails, the stored objects are deleted again."""
    me = _me(db, principal)
    conversation = require_visible(db, me, conversation_id)
    if len(body.strip()) > post_svc.MAX_MEMBER_BODY:     # SPEC-014 A31: before anything is stored
        return PlainTextResponse(f"Messages are limited to {post_svc.MAX_MEMBER_BODY} characters.",
                                 status_code=422)
    if (limited := _throttled(ratelimit.POSTS, me)) is not None:
        return limited
    real = [f for f in files if getattr(f, "filename", "")]
    if len(real) > attachment_svc.MAX_FILES_PER_POST:
        raise ChatInvalid(f"Attach at most {attachment_svc.MAX_FILES_PER_POST} files per message.")
    uploads = []
    try:
        for f in real:
            try:
                uploads.append(store_validated_upload_sync(f, category="chat"))
            except ValueError as exc:
                raise ChatInvalid(str(exc)) from exc
        seq_before = conversation.next_seq
        post = post_svc.create_post(db, conversation, author=me, body=body,
                                    reply_to_id=_uuid_or_none(reply_to_id),
                                    allow_empty=bool(uploads),
                                    client_token=_uuid_or_none(client_token))
        created = post.seq >= seq_before   # a client_token replay returns an older post
        if not created:
            _discard(uploads)              # the replayed post already has its files
        else:
            if uploads:
                attachment_svc.attach(db, post, uploads)
            if post.body:
                hints = [h for h in (_uuid_or_none(x) for x in mention_ids) if h]
                mention_svc.record_mentions(
                    db, post, mention_svc.resolve_mentions(db, conversation, post.body, hints))
        db.commit()   # before the task: dispatch re-reads the post in its own session (A10)
    except Exception:
        db.rollback()
        _discard(uploads)
        raise
    if created and ai_bridge.should_dispatch(conversation, post):
        background.add_task(ai_bridge.dispatch, principal.account_id, conversation.id, post.id)
    return _render_post(request, db, me, post)


@router.post("/posts/{post_id}/edit", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_edit_post(request: Request, post_id: uuid.UUID, body: str = Form(""),
                   principal: RequestPrincipal = require_authenticated(),
                   db: Session = Depends(get_db)):
    me = _me(db, principal)
    target = post_for(db, me, post_id)
    if (limited := _throttled(ratelimit.POSTS, me)) is not None:
        return limited
    post = post_svc.edit_post(db, target, actor=me, body=body)
    db.commit()
    return _render_post(request, db, me, post)


@router.post("/posts/{post_id}/delete", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_delete_post(request: Request, post_id: uuid.UUID,
                     principal: RequestPrincipal = require_authenticated(),
                     db: Session = Depends(get_db)):
    me = _me(db, principal)
    post = post_svc.delete_post(db, post_for(db, me, post_id), actor=me)
    db.commit()
    return _render_post(request, db, me, post)


@router.post("/posts/{post_id}/react", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_react(request: Request, post_id: uuid.UUID, emoji: str = Form(...),
               principal: RequestPrincipal = require_authenticated(),
               db: Session = Depends(get_db)):
    me = _me(db, principal)
    post = post_for(db, me, post_id)
    if (limited := _throttled(ratelimit.REACTIONS, me)) is not None:
        return limited
    reaction_svc.toggle_reaction(db, post, actor=me, emoji=emoji)
    db.commit()
    return _render_post(request, db, me, post)


@router.post("/c/{conversation_id}/read")
@declares("chat.use", Access.ACCOUNT)
def chat_mark_read(conversation_id: uuid.UUID, seq: int = Form(...),
                   principal: RequestPrincipal = require_authenticated(),
                   db: Session = Depends(get_db)):
    me = _me(db, principal)
    conversation = require_visible(db, me, conversation_id)
    # Never past the newest post, or a later post would arrive already "read".
    reads.mark_read(db, me, conversation_id, min(seq, conversation.next_seq - 1))
    db.commit()
    return Response(status_code=204, headers={"HX-Trigger": "chat-read"})


@router.post("/c/{conversation_id}/typing")
@declares("chat.use", Access.ACCOUNT)
def chat_typing(conversation_id: uuid.UUID,
                principal: RequestPrincipal = require_authenticated(),
                db: Session = Depends(get_db)):
    """Memory only (D15): no row, no cursor; at most one per (member, conversation) per 3 s."""
    me = _me(db, principal)
    require_visible(db, me, conversation_id)
    key = (me.id, conversation_id)
    now = time.monotonic()
    if now - _last_typing.get(key, 0.0) >= TYPING_THROTTLE_S:
        if len(_last_typing) > 10_000:   # bounded: stale entries are worthless after 3 s
            _last_typing.clear()
        _last_typing[key] = now
        name = conv.member_name(db, me)
        BUS.publish(me.account_id, {"type": "typing", "c": str(conversation_id),
                                    "m": str(me.id), "name": name})
    return Response(status_code=204)


@router.post("/dm")
@declares("chat.use", Access.ACCOUNT)
def chat_open_dm(request: Request, membership_id: str = Form(...),
                 principal: RequestPrincipal = require_authenticated(),
                 db: Session = Depends(get_db)):
    me = _me(db, principal)
    other = _uuid_or_none(membership_id)
    if other is None:
        raise ChatInvalid("Choose someone to message.")
    conversation = conv.get_or_create_dm(db, me, other)
    db.commit()
    url = f"/chat/c/{conversation.id}"
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


@router.get("/stream")
@declares("chat.use", Access.ACCOUNT)
def chat_stream(request: Request, after: int | None = None,
                principal: RequestPrincipal = require_authenticated(),
                db: Session = Depends(get_db)) -> StreamingResponse:
    """Server-sent events for this tab (D8, D9).

    A sync `def`, so the gate's DB work already ran in the threadpool. **The injected session is
    released here, before the body starts:** `get_db` exits on the request stack, after the body —
    which for a stream is never, so without this every open tab would pin a pooled connection idle
    in transaction (§0.2, A16). `get_db`'s later commit on a closed Session acquires nothing.
    """
    header = request.headers.get("last-event-id")
    after_cursor = int(header) if header and header.isdigit() else after
    db.commit()
    db.close()
    # SPEC-014 D9: at most 6 streams per member and 200 per process; over either, 429 before
    # anything opens. The slot is held for the generator's life and released in its `finally`.
    member = principal.membership_id
    if not streams.acquire(member):
        return PlainTextResponse("Too many chat tabs are open — close one and reload.",
                                 status_code=429, headers={"Retry-After": "30"})

    async def held():
        try:
            async for chunk in stream_events(account_id=principal.account_id,
                                             user_id=principal.user_id, membership_id=member,
                                             session_cookie=request.cookies.get(SESSION_COOKIE),
                                             after_cursor=after_cursor):
                yield chunk
        finally:
            streams.release(member)

    return StreamingResponse(
        held(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/unread-badge", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_unread_badge(request: Request, principal: RequestPrincipal = require_authenticated(),
                      db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "partials/chat/_unread_badge.html", {
        "count": reads.total_unread(db, _me(db, principal)),
    })


@router.get("/search", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_search(request: Request, q: str = "", cursor: str | None = None,
                principal: RequestPrincipal = require_authenticated(),
                db: Session = Depends(get_db)):
    """Full page, or just the results for an HTMX request. Visible, live posts only (A26)."""
    from mihomes.services.chat.render import tokenize_body
    from mihomes.services.chat.search import search_posts

    me = _me(db, principal)
    page = search_posts(db, me, q, cursor=cursor)
    titles = {r.id: r.title for r in conv.list_for(db, me)}
    hits = [{"hit": h, "title": titles.get(h.conversation_id, "Chat"),
             "tokens": tokenize_body(h.snippet)} for h in page.hits]
    ctx = {"q": q, "hits": hits, "next_cursor": page.next_cursor}
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/chat/_search_results.html", ctx)
    return templates.TemplateResponse(request, "chat/search.html", {**ctx, "page": "chat"})


# --- SPEC-014: notification preferences and push devices ------------------------------------
# Every route below reads `principal.membership_id` and accepts no membership id from the request
# (the `gateway.link_self` shape): there is no way to name another member's row.

_MUTE_HOURS = (0, 1, 8, 24)


def _prefs(db: Session, me: Membership):
    from mihomes.models.chat_notify import ChatNotifyPref

    return db.execute(select(ChatNotifyPref).where(
        ChatNotifyPref.membership_id == me.id)).scalar_one_or_none()


@router.get("/notifications", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_notifications(request: Request, saved: bool = False,
                       principal: RequestPrincipal = require_authenticated(),
                       db: Session = Depends(get_db)):
    import os
    from datetime import UTC, datetime

    prefs = _prefs(db, _me(db, principal))
    muted = prefs is not None and prefs.muted_until is not None \
        and prefs.muted_until > datetime.now(UTC)
    return templates.TemplateResponse(request, "partials/chat/_notifications.html", {
        "email_digest": prefs.email_digest if prefs else True,
        "push": prefs.push if prefs else True,
        "muted_until": prefs.muted_until if muted else None,
        "vapid_public_key": os.environ.get("VAPID_PUBLIC_KEY", ""),
        "saved": saved,
    })


@router.post("/notifications", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_save_notifications(request: Request, email_digest: str = Form(""),
                            push: str = Form(""), mute_hours: int = Form(0),
                            principal: RequestPrincipal = require_authenticated(),
                            db: Session = Depends(get_db)):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from mihomes.ids import new_id
    from mihomes.models.chat_notify import ChatNotifyPref

    if mute_hours not in _MUTE_HOURS:
        raise ChatInvalid("Choose how long to mute.")
    me = _me(db, principal)
    values = {"email_digest": bool(email_digest), "push": bool(push),
              "muted_until": (datetime.now(UTC) + timedelta(hours=mute_hours))
              if mute_hours else None}
    stmt = pg_insert(ChatNotifyPref.__table__).values(
        id=new_id(), account_id=me.account_id, membership_id=me.id, **values)
    db.execute(stmt.on_conflict_do_update(index_elements=["account_id", "membership_id"],
                                          set_=values))
    db.commit()
    return chat_notifications(request, saved=True, principal=principal, db=db)


@router.post("/push/subscribe")
@declares("chat.use", Access.ACCOUNT)
async def chat_push_subscribe(request: Request,
                              principal: RequestPrincipal = require_authenticated(),
                              db: Session = Depends(get_db)):
    """Body: the browser's `PushSubscription.toJSON()`. 204, or 422 on a disallowed push service
    or malformed keys (D6) — and no row is written."""
    from starlette.concurrency import run_in_threadpool

    from mihomes.services.chat import push as push_svc

    try:
        data = await request.json()
        endpoint = str(data["endpoint"])
        keys = data.get("keys") or {}
        p256dh, auth = str(keys["p256dh"]), str(keys["auth"])
    except (ValueError, KeyError, TypeError):
        return PlainTextResponse("That isn't a push subscription.", status_code=422)

    def save():
        push_svc.subscribe(db, account_id=principal.account_id,
                           membership_id=principal.membership_id, endpoint=endpoint,
                           p256dh=p256dh, auth=auth,
                           user_agent=request.headers.get("user-agent"))
        db.commit()

    try:
        await run_in_threadpool(save)
    except ValueError as exc:
        logger.info("chat push: subscription refused: %s", exc)
        return PlainTextResponse(str(exc), status_code=422)
    return Response(status_code=204)


@router.post("/push/unsubscribe")
@declares("chat.use", Access.ACCOUNT)
async def chat_push_unsubscribe(request: Request,
                                principal: RequestPrincipal = require_authenticated(),
                                db: Session = Depends(get_db)):
    """Removes only the caller's own row for that endpoint. 204 whether or not one matched."""
    from starlette.concurrency import run_in_threadpool

    from mihomes.services.chat import push as push_svc

    try:
        endpoint = str((await request.json())["endpoint"])
    except (ValueError, KeyError, TypeError):
        return PlainTextResponse("Missing endpoint.", status_code=422)

    def remove():
        push_svc.unsubscribe(db, membership_id=principal.membership_id, endpoint=endpoint)
        db.commit()

    await run_in_threadpool(remove)
    return Response(status_code=204)


@router.get("/c/{conversation_id}/mentionable", response_class=HTMLResponse)
@declares("chat.use", Access.ACCOUNT)
def chat_mentionable(request: Request, conversation_id: uuid.UUID, q: str = "",
                     principal: RequestPrincipal = require_authenticated(),
                     db: Session = Depends(get_db)):
    """The @-picker: only people who can see this conversation (A23)."""
    me = _me(db, principal)
    conversation = require_visible(db, me, conversation_id)
    return templates.TemplateResponse(request, "partials/chat/_mentionable.html", {
        "people": mention_svc.mentionable(db, conversation, q=q[:60], exclude=me.id),
    })


@router.get("/file/{attachment_id}")
@declares("chat.use", Access.ACCOUNT)
def chat_file(attachment_id: uuid.UUID, principal: RequestPrincipal = require_authenticated(),
              db: Session = Depends(get_db)) -> Response:
    """SPEC-013 D12: 404 for every refusal; always proxied, never a presigned redirect."""
    from mihomes.storage import ObjectNotFound, get_storage

    att = attachment_svc.load_for_download(db, _me(db, principal), attachment_id)
    try:
        data = get_storage().get(att.storage_key)
    except ObjectNotFound as exc:
        raise ChatNotFound(str(attachment_id)) from exc
    return Response(content=data, media_type=att.content_type,
                    headers=attachment_svc.download_headers(att))


def _discard(uploads) -> None:
    """Delete stored objects that will not be recorded — no orphans (§5.4 step 4)."""
    from mihomes.storage import get_storage

    for u in uploads:
        try:
            get_storage().delete(u.key)
        except Exception:  # pragma: no cover - best effort, but never silent (N15)
            logger.exception("chat upload: could not delete orphaned object %s", u.key)


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError as exc:
        raise ChatInvalid("That link is broken.") from exc
