"""Web push for chat (SPEC-014 §5.3, D5–D7, D14).

**The lock screen is the reader to worry about**, not the push service (Web Push is end-to-end
encrypted to the browser, RFC 8291). So a payload never carries message text or a sender name, and
a DM's title is the fixed "Direct message" (D7).

**Endpoints are allowlisted (D6)**: the job POSTs to a URL a client supplied, so without the list a
member could point it at `http://169.254.169.254/…` or a tailnet host — SSRF from inside the
deployment. Checked at subscribe time and again at send time.

`pywebpush` is imported only inside `WebPushSender`, so the job and the routes work (and push is
simply off) when VAPID is unconfigured.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from mihomes.ids import new_id
from mihomes.models.chat_native import KIND_DM, ChatConversation
from mihomes.models.chat_notify import PushSubscription

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_SUBSCRIPTIONS_PER_MEMBER", "PUSH_HOSTS", "PushResult", "PushSender", "WebPushSender",
    "build_payload", "endpoint_allowed", "generate_vapid_keys", "send_pushes", "sender_from_env",
    "subscribe", "unsubscribe",
]

PUSH_HOSTS = frozenset({"fcm.googleapis.com", "updates.push.services.mozilla.com",
                        "web.push.apple.com"})
PUSH_HOST_SUFFIXES = (".notify.windows.com",)
PUSH_TTL_SECONDS = 6 * 3600       # older than this is noise; the digest carries it
MAX_SUBSCRIPTIONS_PER_MEMBER = 10  # oldest evicted on subscribe


def endpoint_allowed(endpoint: str) -> bool:
    """https only, no userinfo, no explicit port, and a known push-service host."""
    try:
        parts = urlsplit(endpoint or "")
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or port is not None:
        return False
    return host in PUSH_HOSTS or any(host.endswith(s) and host != s.lstrip(".")
                                     for s in PUSH_HOST_SUFFIXES)


def build_payload(conversation: ChatConversation, reason: str, *, title: str) -> dict:
    """D7/O3. `title` is the group's title (General / the property); a DM never shows a name.
    Never reads a post body."""
    return {
        "title": "Direct message" if conversation.kind == KIND_DM else title,
        "body": "You were mentioned" if reason == "mention" else "New message",
        "url": f"/chat/c/{conversation.id}",
        "tag": f"chat-{conversation.id}",
    }


def _b64url_len(value: str, expected: int) -> bool:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        return False
    return len(raw) == expected


def subscribe(session: Session, *, account_id: uuid.UUID, membership_id: uuid.UUID,
              endpoint: str, p256dh: str, auth: str, user_agent: str | None) -> None:
    """Upsert the caller's subscription. ValueError on a disallowed endpoint or bad keys."""
    if not endpoint_allowed(endpoint):
        raise ValueError("That push service isn't supported.")
    if not _b64url_len(p256dh or "", 65) or not _b64url_len(auth or "", 16):
        raise ValueError("The browser sent invalid push keys.")
    stmt = pg_insert(PushSubscription.__table__).values(
        id=new_id(), account_id=account_id, membership_id=membership_id, endpoint=endpoint,
        p256dh=p256dh, auth=auth, user_agent=(user_agent or "")[:300] or None)
    session.execute(stmt.on_conflict_do_update(
        constraint="uq_push_subscriptions_member_endpoint",
        set_={"p256dh": stmt.excluded.p256dh, "auth": stmt.excluded.auth,
              "user_agent": stmt.excluded.user_agent}))
    rows = session.execute(
        select(PushSubscription.id).where(PushSubscription.membership_id == membership_id)
        .order_by(PushSubscription.created_at.desc())).scalars().all()
    if len(rows) > MAX_SUBSCRIPTIONS_PER_MEMBER:
        session.execute(delete(PushSubscription).where(
            PushSubscription.id.in_(rows[MAX_SUBSCRIPTIONS_PER_MEMBER:])))


def unsubscribe(session: Session, *, membership_id: uuid.UUID, endpoint: str) -> int:
    """Only ever the caller's own row: the predicate includes the caller."""
    return session.execute(delete(PushSubscription).where(
        PushSubscription.membership_id == membership_id,
        PushSubscription.endpoint == endpoint)).rowcount or 0


class PushSender(Protocol):
    def send(self, sub: PushSubscription, payload: dict) -> int: ...   # HTTP status, 0 = network


class WebPushSender:
    def __init__(self, *, private_key: str, subject: str, timeout: float = 10.0) -> None:
        from py_vapid import Vapid

        # Parsed once: a str key makes pywebpush re-parse it on every send.
        self._key = Vapid.from_string(private_key)
        self._subject, self._timeout = subject, timeout

    def send(self, sub: PushSubscription, payload: dict) -> int:
        from pywebpush import WebPushException, webpush

        try:
            webpush(subscription_info={"endpoint": sub.endpoint,
                                       "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                    data=json.dumps(payload), vapid_private_key=self._key,
                    # A FRESH dict per call: pywebpush writes `aud` into the dict it is given,
                    # only when absent — a reused dict would sign every later host's push with
                    # the first host's audience (403 everywhere but the first).
                    vapid_claims={"sub": self._subject},
                    ttl=PUSH_TTL_SECONDS, timeout=self._timeout)
            return 201
        except WebPushException as exc:
            return exc.response.status_code if exc.response is not None else 0
        except Exception:
            logger.warning("chat push: send failed", exc_info=True)
            return 0


_warned = False


def sender_from_env() -> WebPushSender | None:
    """None (logged once) when VAPID is unconfigured: push off, digests unaffected (D14)."""
    global _warned
    key, subject = os.environ.get("VAPID_PRIVATE_KEY", ""), os.environ.get("VAPID_SUBJECT", "")
    if not key or not subject:
        if not _warned:
            logger.warning("chat push disabled: VAPID_PRIVATE_KEY / VAPID_SUBJECT not set")
            _warned = True
        return None
    return WebPushSender(private_key=key, subject=subject)


@dataclass
class PushResult:
    sent: int = 0
    removed: int = 0
    failed: int = 0


def send_pushes(session: Session, pushes: list[tuple[uuid.UUID, dict]],
                sender: PushSender | None) -> PushResult:
    """Each payload to every allowed subscription of its member. 2xx → last_success_at;
    404/410 → the browser revoked it, delete; anything else → keep, no retry (at-most-once)."""
    result = PushResult()
    if sender is None or not pushes:
        return result
    for membership_id, payload in pushes:
        subs = session.execute(select(PushSubscription).where(
            PushSubscription.membership_id == membership_id)).scalars().all()
        for sub in subs:
            if not endpoint_allowed(sub.endpoint):
                continue
            status = sender.send(sub, payload)
            if 200 <= status < 300:
                sub.last_success_at = datetime.now(timezone.utc)
                result.sent += 1
            elif status in (404, 410):
                session.delete(sub)
                result.removed += 1
            else:
                result.failed += 1
    session.flush()
    return result


def generate_vapid_keys() -> tuple[str, str]:
    """(public, private), base64url. Public = the 65-byte uncompressed point (the browser's
    applicationServerKey); private = the raw 32-byte scalar, which py-vapid reads directly.
    Run: python -c "from mihomes.services.chat.push import generate_vapid_keys as g; print(*g(), sep='\\n')"
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, "big")

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return b64(public), b64(private)
