"""Webhook verification and envelope normalization — SPEC-006 §5.4, Step 5 (D7/N4).

Two verify functions and one normalizer. Deliberately **pure**: no session, no I/O, no
framework types. The route (`web/routes/gateways.py`) does the I/O; this module does the
deciding, so both halves can be tested without the other.

## N4 — verify before any parse, and what that means on each transport

*"Do not parse a webhook body before verifying its signature. SPEC-004 N3 verbatim, different
vendor. Raw bytes first."* The reasoning is exact for an HMAC: a framework that hands you a
parsed body has already re-serialized it — key order, whitespace and unicode escaping all
change — so the signature either fails to match or, worse, matches *after* you acted on
unverified input.

**The two transports are not equally strong, and the difference matters enough to state:**

| | mechanism | is `raw_body` actually used? |
|---|---|---|
| Telegram | a caller-chosen secret echoed in `X-Telegram-Bot-Api-Secret-Token` | **no** — Telegram signs nothing |
| WhatsApp Cloud API | HMAC-SHA256 over the raw body, in `X-Hub-Signature-256` | **yes** |

So on the Telegram path N4 survives as *ordering discipline* rather than as a cryptographic
guarantee over bytes: the secret token is compared before the body is touched, which is the
same shape, but a forged body from someone who has the secret is indistinguishable. That is
Telegram's design, not a shortcut taken here — and it is why `verify_telegram` still takes
`raw_body` it does not read, so the call site cannot drift into parsing first when the WhatsApp
route (Step 7) starts sharing this module.

Both comparisons are `compare_digest`. A `==` on a secret leaks its length and prefix through
timing, which is the one bug in this file that no test would ever catch.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

logger = logging.getLogger(__name__)

__all__ = [
    "TELEGRAM_SECRET_HEADER",
    "WHATSAPP_SIGNATURE_HEADER",
    "WebhookVerificationError",
    "normalize_telegram_update",
    "verify_telegram",
    "verify_whatsapp",
]

TELEGRAM_SECRET_HEADER = "x-telegram-bot-api-secret-token"
WHATSAPP_SIGNATURE_HEADER = "x-hub-signature-256"


class WebhookVerificationError(Exception):
    """The request did not authenticate. Raised before anything is parsed or written.

    Deliberately carries no detail about *which* comparison failed and never the expected
    value: on a failed verification the caller is unauthenticated by definition, and a
    discriminating error message is a probing oracle.
    """


def verify_telegram(raw_body: bytes, secret_token_header: str | None, *, expected: str) -> None:
    """Telegram's `secret_token` echo. Raises `WebhookVerificationError` on mismatch.

    `raw_body` is accepted and **not read** — see the module docstring. It is in the signature
    so this function and `verify_whatsapp` are interchangeable at the call site, which is what
    keeps the "verify before parse" ordering true for both when the route grows a second
    transport.

    An empty `expected` is a **configuration error, not a pass**. `setWebhook` without a
    `secret_token` leaves the endpoint open to anyone who learns the URL, so an unconfigured
    secret refuses every request rather than accepting every request — the difference between
    failing closed and failing open, decided here rather than at the call site.
    """
    if not expected:
        raise WebhookVerificationError("no webhook secret is configured for telegram")
    if not secret_token_header:
        raise WebhookVerificationError("missing secret token header")
    if not hmac.compare_digest(secret_token_header, expected):
        raise WebhookVerificationError("secret token mismatch")


def verify_whatsapp(raw_body: bytes, signature_header: str | None, *, app_secret: str) -> None:
    """Cloud API HMAC-SHA256 over the **raw** body (`X-Hub-Signature-256`).

    This is the one that genuinely verifies bytes, and the reason `raw_body` must never have
    been through a parse-and-reserialize round trip before it arrives here.

    Meta sends the digest prefixed `sha256=`. The prefix is stripped before comparison rather
    than folded into the expected string, so a request that omits it fails on the digest rather
    than on the formatting.
    """
    if not app_secret:
        raise WebhookVerificationError("no app secret is configured for whatsapp")
    if not signature_header:
        raise WebhookVerificationError("missing signature header")

    provided = signature_header
    if provided.startswith("sha256="):
        provided = provided[len("sha256=") :]

    expected = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(provided, expected):
        raise WebhookVerificationError("signature mismatch")




def normalize_telegram_update(raw_body: bytes) -> dict | None:
    """One verified Telegram webhook body → the internal message envelope, or `None`.

    Parsing is all this adds: the envelope itself comes from `telegram.client.normalize_message`,
    the same function the poller uses, so the two transports cannot drift again (they had — see
    its docstring). The shape is the one `review_common` consumes; a missing key is not a
    degraded envelope but a message the responder silently discards, which G5 learned the hard
    way.

    Returns `None` for an update carrying nothing we act on — an edited message, a poll answer,
    a `my_chat_member` change, a member joining. That is not an error: Telegram sends many update
    kinds and a handler that raised on the unfamiliar ones would turn routine traffic into 500s,
    which providers retry.

    **Called only after verification.** The parse itself is the step N4 orders last.
    """
    from mihomes.services.gateways.telegram.client import normalize_message

    try:
        update = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # A verified caller sending unparseable bytes is a bug on their side or ours; either
        # way there is no message here. `None` lets the route ack rather than retry forever.
        logger.warning("telegram webhook: verified body did not parse as JSON")
        return None

    return normalize_message(update)
