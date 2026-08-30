"""WhatsApp Cloud API client — SPEC-006 §5.5, Step 7 (D10, F3).

Implements the **existing** `WhatsAppBridge` Protocol (`whatsapp/protocol.py`) structurally: no
subclassing, no registration, no base class. That Protocol has had **zero implementers since it
was written** (F3), and its four methods — `send_message`, `send_template`,
`get_message_status`, `register_webhook` — are the Cloud API's shape rather than Baileys'. That
is not a coincidence: it was written against the API this module finally talks to, and Step 7 is
the cheapest thing in the phase because the seam was built first.

## Structural conformance, and why not `isinstance`

`WhatsAppBridge` is a plain `Protocol`, not `@runtime_checkable`, so `isinstance` would raise
rather than answer. It is also the wrong question. D10 asks whether this class *satisfies the
shape*, and the AIProvider precedent in this codebase answers that the same way: match the
methods and their signatures, inherit nothing. A18 asserts both halves — every method conforms,
**and** `CloudAPIClient` is not a subclass — because "structurally, no subclassing" is two
claims and a test that checked only the first would pass on a subclass.

## What the responders never learn

Nothing here is reachable from a responder. They hold a `GatewayAdapter` (D2), whose `send`
closes over whichever client is configured — so swapping Baileys for the Cloud API changes the
closure and no responder line (A20). The adapter is the seam; this is one thing behind it.

## O1 is open, and this module is built to survive either answer (U2)

The tier question — whether the chosen Cloud API plan supports **group** messaging — is a
founder cost/capability call, and everything here is tier-independent. But if the tier drops
groups it is **a loss of function, not a transport swap**: the live product routes an inventory
*group* through `whatsapp.inventory_group_jid`, and that key needs a replacement routing
concept, not a rename. `send_group_message` is therefore present and raises a **named** error
rather than silently falling back to per-recipient sends — a fallback would look like it worked
while quietly changing who receives an estate's inventory. §7's words: a behaviour change the
migration must *state*, not absorb.

## Coverage

Omitted today by `*/services/gateways/whatsapp/*`, and **stays omitted after G10** on the same
network-bound reasoning that omits `stripe_provider.py` and the AI providers (U8). The seam
worth testing is the Protocol boundary, and `FakeCloudClient` tests that without credentials.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["CloudAPIClient", "CloudAPIError", "GroupsNotSupported", "normalize_cloud_message"]

#: Meta's Graph API version. Pinned, not floating: a version bump changes payload shapes, and
#: discovering that from a production 400 is worse than an explicit upgrade.
GRAPH_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"


class CloudAPIError(Exception):
    """A Cloud API call failed. Carries a caller-safe message."""


class GroupsNotSupported(CloudAPIError):
    """This tier has no group messaging — O1's open question landing at runtime (U2).

    Named rather than generic, and raised rather than degraded, because the alternative is
    worse than an error: silently sending to individuals instead of a group would look like the
    feature working while the estate's inventory reports went to one person.
    """


class CloudAPIClient:
    """Structural implementation of `WhatsAppBridge` over Meta's Cloud API.

    Credentials come from the environment rather than `configurations`: this client is
    constructed at the transport edge, before any account is bound, which is the same bootstrap
    constraint the webhook secret has (§5.1). Per-account credentials are deferred (§7, U6).
    """

    def __init__(
        self,
        access_token: str | None = None,
        phone_number_id: str | None = None,
        *,
        supports_groups: bool = False,
    ):
        self.access_token = access_token or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
        self.phone_number_id = phone_number_id or os.environ.get(
            "WHATSAPP_PHONE_NUMBER_ID", ""
        )
        # Defaults to False — the fail-closed direction while O1 is open. A tier that *does*
        # support groups is a deliberate opt-in, so an unconfigured install refuses loudly
        # instead of discovering the limitation in production.
        self.supports_groups = supports_groups

    # -- WhatsAppBridge ---------------------------------------------------------------

    def send_message(self, phone_number: str, message: str) -> dict:
        """Send a text message to an E.164 number. Returns `{message_id, status}`."""
        return self._post(
            "messages",
            {
                "messaging_product": "whatsapp",
                "to": phone_number,
                "type": "text",
                "text": {"body": message},
            },
        )

    def send_template(
        self,
        phone_number: str,
        template_name: str,
        parameters: dict | None = None,
    ) -> dict:
        """Send a pre-approved template.

        Outside the 24-hour customer-service window the Cloud API accepts **only** templates,
        which is a real behavioural difference from Baileys — where any message could be sent
        at any time. Callers that reply to a stale conversation need this path, not
        `send_message`, and will get an API error otherwise rather than a silent drop.
        """
        components: list[dict[str, Any]] = []
        if parameters:
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(v)} for v in parameters.values()
                    ],
                }
            )
        return self._post(
            "messages",
            {
                "messaging_product": "whatsapp",
                "to": phone_number,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": "en_US"},
                    **({"components": components} if components else {}),
                },
            },
        )

    def get_message_status(self, message_id: str) -> dict:
        """Delivery status for a sent message.

        The Cloud API reports status by **webhook callback**, not by polling — there is no
        "fetch the status of this id" endpoint. Returning `unknown` is therefore the honest
        answer for a Protocol method whose shape predates that knowledge, rather than inventing
        a request that would 404. The real path is `statuses` entries arriving at the webhook.
        """
        return {"message_id": message_id, "status": "unknown"}

    def register_webhook(self, callback_url: str) -> bool:
        """Register a callback URL.

        **Always False**, deliberately, and not a stub: Cloud API webhooks are configured in
        the Meta App Dashboard, not through an API call — there is no endpoint to POST to. A
        method that returned True would claim a registration nobody performed, which is exactly
        the shape of U3's open question one transport over.
        """
        logger.info(
            "register_webhook: Cloud API webhooks are configured in the Meta App Dashboard; "
            "no API call performed for %s",
            callback_url,
        )
        return False

    # -- beyond the Protocol ----------------------------------------------------------

    def send_group_message(self, group_jid: str, text: str) -> dict:
        """Send to a group — **O1's open question, surfaced as an error not a fallback** (U2).

        Kept on the class because `GatewayAdapter`'s WhatsApp `send` closes over exactly this
        method today (`responder.py`), so its absence would be a responder change and A20 says
        there is none.
        """
        if not self.supports_groups:
            raise GroupsNotSupported(
                "This WhatsApp Cloud API tier does not support group messaging (O1). "
                "`whatsapp.inventory_group_jid` needs a replacement routing key before the "
                "inventory group can move off Baileys — a behaviour change, not a swap."
            )
        return self._post(
            "messages",
            {
                "messaging_product": "whatsapp",
                "to": group_jid,
                "type": "text",
                "text": {"body": text},
            },
        )

    # -- transport --------------------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:  # pragma: no cover - network
        """One POST to the Graph API. Network-bound, hence coverage-omitted (U8)."""
        import httpx

        if not self.access_token or not self.phone_number_id:
            raise CloudAPIError(
                "WhatsApp Cloud API is not configured — set WHATSAPP_ACCESS_TOKEN and "
                "WHATSAPP_PHONE_NUMBER_ID."
            )

        url = f"{GRAPH_BASE}/{self.phone_number_id}/{path}"
        try:
            r = httpx.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            raise CloudAPIError(f"WhatsApp Cloud API request failed: {e}") from e

        messages = data.get("messages") or [{}]
        return {"message_id": messages[0].get("id", ""), "status": "sent"}


def normalize_cloud_message(entry: dict, *, property_slug: str | None = None) -> dict | None:
    """One Cloud API inbound message → **the same eleven-key dict Baileys produced** (A19).

    The contract is `telegram/client.py:164`'s envelope, which both existing responders already
    consume: `id`, `timestamp`, `jid`, `isGroup`, `sender`, `senderName`, `senderUsername`,
    `text`, `hasMedia`, `mediaPath`, `propertySlug`.

    **"The same dict", not "a compatible subset"** — and that distinction is not pedantry. G5
    shipped a webhook envelope missing `propertySlug`, and `responder.py:208` filters on exactly
    that key, so every message was dropped before dispatch while the tests still passed. A
    missing key is not a degraded envelope; it is a message the responder silently discards.

    Returns `None` for a payload carrying no message — a `statuses` callback (delivery receipts),
    or an entry shape we do not act on. Not an error: Meta sends several callback kinds to one
    URL, and raising on the unfamiliar ones would turn routine traffic into retries.
    """
    try:
        value = entry["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return None

    messages = value.get("messages")
    if not messages:
        # A delivery-status callback rather than an inbound message.
        return None

    msg = messages[0]
    contacts = value.get("contacts") or [{}]
    profile = (contacts[0].get("profile") or {}) if contacts else {}

    wa_id = msg.get("from") or ""
    msg_type = msg.get("type") or "text"
    has_media = msg_type in ("image", "video", "document", "audio", "sticker")

    text = ""
    if msg_type == "text":
        text = (msg.get("text") or {}).get("body") or ""
    elif has_media:
        # Media captions carry the human's actual words; without this an inventory photo
        # arrives with no text at all and the analyzer has nothing to categorize.
        text = (msg.get(msg_type) or {}).get("caption") or ""

    return {
        "id": msg.get("id") or "",
        "timestamp": msg.get("timestamp"),
        # `jid` holds the WhatsApp id. In a group the Cloud API reports the group in
        # `value.metadata`; per-message it is the sender's wa_id, so a group-capable tier fills
        # this from metadata and a 1:1 tier from `from` — one key either way (D2).
        "jid": str((value.get("metadata") or {}).get("display_phone_number") or wa_id),
        "isGroup": bool((value.get("metadata") or {}).get("group_id")),
        "sender": str(wa_id),
        "senderName": profile.get("name") or "",
        # The Cloud API has no usernames — WhatsApp identity is the phone number. Empty rather
        # than absent, so the key's presence does not vary by transport.
        "senderUsername": "",
        "text": text,
        "hasMedia": has_media,
        # Media needs a second authenticated GET against the media id; the responder fetches
        # lazily, exactly as the Telegram path leaves `mediaPath` unset at normalize time.
        "mediaPath": None,
        "propertySlug": property_slug,
    }
