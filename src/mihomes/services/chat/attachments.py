"""Chat attachments: recording uploads and authorising downloads (SPEC-013 D11–D13).

A download is allowed only for a member who can see the post's conversation, for a post that is
not deleted, and for a key under the member's own account. Every refusal is the same 404, so an
attachment id is not probeable. Bytes are always proxied, never a presigned redirect: a redirect
would drop the `Content-Disposition`/`nosniff`/`sandbox` headers below.
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.chat_native import ChatAttachment, ChatPost
from mihomes.models.membership import Membership
from mihomes.services.chat.access import ChatNotFound, visible_conversation_ids
from mihomes.storage import key_account

__all__ = ["IMAGE_TYPES", "MAX_FILES_PER_POST", "attach", "attachments_for",
           "download_headers", "load_for_download"]

MAX_FILES_PER_POST = 5
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


def attach(session: Session, post: ChatPost, uploads) -> list[ChatAttachment]:
    rows = [ChatAttachment(account_id=post.account_id, post_id=post.id, storage_key=u.key,
                           filename=u.filename, content_type=u.content_type, size_bytes=u.size)
            for u in uploads]
    session.add_all(rows)
    session.flush()
    return rows


def attachments_for(session: Session, post_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ChatAttachment]]:
    """Batch lookup for rendering a page of posts (one query)."""
    out: dict[uuid.UUID, list[ChatAttachment]] = {}
    if not post_ids:
        return out
    for att in session.execute(select(ChatAttachment).where(ChatAttachment.post_id.in_(post_ids))
                               .order_by(ChatAttachment.created_at)).scalars():
        out.setdefault(att.post_id, []).append(att)
    return out


def load_for_download(session: Session, membership: Membership,
                      attachment_id: uuid.UUID) -> ChatAttachment:
    row = session.execute(
        select(ChatAttachment, ChatPost.conversation_id, ChatPost.deleted_at)
        .join(ChatPost, ChatPost.id == ChatAttachment.post_id)
        .where(ChatAttachment.id == attachment_id)
    ).one_or_none()
    if (row is None or row.deleted_at is not None
            or row.conversation_id not in visible_conversation_ids(session, membership)
            or key_account(row.ChatAttachment.storage_key) != str(membership.account_id)):
        raise ChatNotFound(str(attachment_id))
    return row.ChatAttachment


def download_headers(att: ChatAttachment) -> dict[str, str]:
    """D13: only images inline; the filename can never break out of the header."""
    disposition = "inline" if att.content_type in IMAGE_TYPES else "attachment"
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", att.filename)[:120] or "file"
    return {
        "Content-Disposition": (f'{disposition}; filename="{ascii_name}"; '
                                f"filename*=UTF-8''{quote(att.filename[:200], safe='')}"),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "sandbox; default-src 'none'",
    }
