"""Conversations — read a bound group chat in the app (2026-10-27).

Owners and admins only. `audit.view` is the matrix's "read the account's historical record" key —
the same honest-but-approximate choice `ai.py` makes for stored AI transcripts; a dedicated
`conversation.view` key would be a matrix change (pinned row and key counts), so it is not made
here. Staff are in the group already; the app does not re-show it to them.

Messages are written from `review_common.handle_inbound`; this module only reads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.services import property as prop_svc
from mihomes.services.gateways import conversation_log as log
from mihomes.web.deps import get_db, require_authenticated, templates

router = APIRouter()

_CHANNEL_NAMES = {"telegram": "Telegram", "whatsapp": "WhatsApp"}


def _label(convo: dict, property_names: dict[str, str]) -> str:
    channel = _CHANNEL_NAMES.get(convo["channel"], convo["channel"].title())
    house = property_names.get(convo.get("property_slug") or "")
    return f"{channel} group · {house}" if house else f"{channel} group"


@router.get("/")
@declares("audit.view", Access.ACCOUNT)
def conversations_page(
    request: Request,
    channel: str = "",
    chat: str = "",
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    names = {p.slug: p.name for p in prop_svc.list_properties(db)}
    convos = log.list_conversations(db)
    for c in convos:
        c["label"] = _label(c, names)
    selected = next(
        (c for c in convos if c["channel"] == channel and c["chat_id"] == chat),
        convos[0] if convos else None,
    )
    return templates.TemplateResponse(request, "conversations.html", {
        "page": "conversations",
        "conversations": convos,
        "selected": selected,
        "messages": (
            log.conversation_messages(db, selected["channel"], selected["chat_id"])
            if selected else []
        ),
    })


@router.get("/messages", response_class=HTMLResponse)
@declares("audit.view", Access.ACCOUNT)
def conversation_messages_partial(
    request: Request,
    channel: str,
    chat: str,
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """The message list alone — what the open conversation polls."""
    return templates.TemplateResponse(request, "partials/conversation_messages.html", {
        "channel": channel,
        "chat": chat,
        "messages": log.conversation_messages(db, channel, chat),
    })
