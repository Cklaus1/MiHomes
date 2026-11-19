"""The internal message envelope every channel normalizes into (OMNICHANNEL_GATEWAY_PRD §7).

`review_common` and the responders consume exactly this shape, whatever the transport. A missing
key is not a degraded envelope: `responder` filters on `propertySlug`, so a normalizer that left a
key out had its messages silently dropped before dispatch (G5). The keys are camelCase because
that is what the live code and the Baileys bridge have always produced; the PRD follows the code.

`channel` names the messaging app ("telegram", "whatsapp"), not the transport: the Baileys bridge
and the Cloud API are two transports of one channel and produce the same envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypedDict


class InternalMessage(TypedDict):
    id: str                   # dedup key — stable across redelivery and across transports
    timestamp: str            # ISO-8601, UTC
    channel: str              # "telegram" | "whatsapp" | later "sms", "email"
    jid: str                  # the conversation — where a reply goes (chat id / wa_id / group jid)
    isGroup: bool
    sender: str               # the channel's user id (Telegram user id, WhatsApp wa_id/phone)
    senderName: str
    senderUsername: str       # "" where the channel has no usernames
    text: str
    hasMedia: bool
    mediaPath: str | None     # local file once fetched; None until then
    propertySlug: str | None  # filled from the chat→property map once the account is bound


#: Every key an envelope must carry — the contract tests compare normalizers against this.
ENVELOPE_KEYS: frozenset[str] = frozenset(InternalMessage.__annotations__)


def iso_utc(epoch_seconds: int | float | str | None) -> str:
    """A provider's epoch timestamp as an ISO-8601 UTC string (the envelope's one format)."""
    return datetime.fromtimestamp(int(epoch_seconds or 0), tz=timezone.utc).isoformat()
