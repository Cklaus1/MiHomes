"""`POST /webhooks/telegram` — chat gateway ingress. SPEC-006 §5.4, Step 5.

**The second route in the app with no session and no tenant scope**, and it earns that the same
way `webhooks.py` does: the caller is not a user. Telegram POSTs from its own infrastructure
with no cookie, no principal and no account, so there is no role for the matrix to consult. What
authenticates it is the secret token echoed in `X-Telegram-Bot-Api-Secret-Token`, compared with
`compare_digest` before the body is touched (D7/N4).

## The five steps, in this order, and why the order is the design

§5.4 numbers them and D11 is why they cannot be reordered:

1. read the **raw** body, verify (N4)
2. normalize the envelope to the dict the responders already expect
3. `resolve_sender` → account, **or reply with a linking prompt** (D12/N2)
4. open a scoped session for *that* account
5. hand to `process_and_respond`

Step 3 before step 4 is the whole phase in one line: which account to scope to is precisely what
the sender lookup determines, so the lookup itself runs unscoped (§5.1's carve-out) and every
write after it runs inside the account it returned. An unlinked sender never reaches step 4 —
they get a linking prompt, never a default account (N2).

## Why dedup happens *inside* the scoped session, not at the edge

F7 says redelivery is already idempotent via `ProcessedIdStore`. Measured, rather than assumed:
`ProcessedIdStore` opens its own `get_session()` and reads `Configuration`, which is a
`TENANT_TABLES` model — so calling it with no account bound raises `LookupError` from the
fail-closed tenancy filter. Probed directly; both `contains()` and `add()` raise.

So dedup runs after step 3, per account, which has a consequence worth stating rather than
discovering later: **an unlinked sender's redeliveries are not deduped**, because they never
reach a scoped session. They re-receive the linking prompt on each retry. That is the acceptable
side of the trade — the alternative is a transport-level store that must be readable before any
account is known, which is a new unscoped write path in the one part of the system where D12
says there must not be one.

## Status codes: same reasoning as the Stripe route, different retry appetite

Telegram retries non-2xx deliveries. So a permanent failure must not return one:

- **bad or missing secret token → 401.** Not a retry candidate: an attack, or a misconfigured
  `setWebhook`. Both need to be visible rather than absorbed.
- **anything processed, ignored, or from an unlinked sender → 200.** Including updates we
  deliberately do not act on, so Telegram stops sending them.
- **an unexpected server error → 500**, so Telegram *does* retry — the one case where a retry is
  what we want, and A16's idempotency is what makes it safe.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from mihomes.services.gateways.identity import (
    AmbiguousSender,
    UnlinkedSender,
    resolve_sender,
)
from mihomes.services.gateways.webhook import (
    TELEGRAM_SECRET_HEADER,
    WebhookVerificationError,
    normalize_telegram_update,
    verify_telegram,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: What an unlinked sender is told (D12). Deliberately does **not** name any account: the
#: sender is unknown, so confirming which estates exist would leak tenant names to anyone who
#: can find the bot.
LINK_PROMPT = (
    "I don't recognise you yet. Ask an owner or admin for a link code, then send: /link <code>"
)


@router.post("/webhooks/telegram")
async def telegram_webhook(request: Request) -> Response:
    """Verify, normalize, resolve, scope, dispatch. **No `Depends(get_db)`.**

    The injected `get_db` binds tenant context from the request's principal, and there is no
    principal here — establishing the tenant is steps 3 and 4's job. Opening the session
    explicitly keeps that asymmetry visible at the one place it applies, exactly as the Stripe
    route does.
    """
    # --- 1. raw body, verified before anything is parsed (N4) ----------------------------
    raw_body = await request.body()
    secret_header = request.headers.get(TELEGRAM_SECRET_HEADER)

    try:
        expected = _configured_secret()
        verify_telegram(raw_body, secret_header, expected=expected)
    except WebhookVerificationError:
        # Logged without the body or the header: both are attacker-controlled on a failed
        # verification, and a log line is where untrusted bytes get read later.
        logger.warning("telegram webhook: verification failed")
        return Response(content="Unauthorized.", status_code=401)

    # --- 2. normalize ---------------------------------------------------------------------
    message = normalize_telegram_update(raw_body)
    if message is None or not message.get("sender"):
        # An update kind we do not act on, or one with no sender to resolve. Ack so Telegram
        # stops resending it.
        return Response(status_code=200)

    # --- 3. resolve the sender — UNSCOPED, and the one place that is legitimate -----------
    from mihomes.db import get_session

    try:
        with get_session() as unscoped:
            resolved = resolve_sender(
                unscoped, gateway="telegram", sender_id=message["sender"]
            )
    except UnlinkedSender:
        _reply(message["jid"], LINK_PROMPT)
        return Response(status_code=200)
    except AmbiguousSender:
        _reply(
            message["jid"],
            "You're linked in more than one account. Send this from the group for the estate "
            "you mean, rather than a direct message.",
        )
        return Response(status_code=200)

    # --- 4 & 5. scope to the resolved account, then dispatch -----------------------------
    _dispatch(resolved, message)
    return Response(status_code=200)


def _configured_secret() -> str:
    """The `setWebhook` secret token, read outside any tenant context.

    Read from the environment rather than `configurations`, deliberately: that table is
    `TENANT_TABLES`, and this value is needed *before* an account exists — the same bootstrap
    problem the sender lookup has, and the same answer. Per-account bot tokens are deferred
    (§7, U6), so one secret serves the one bot.
    """
    import os

    return os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")


def _property_for_chat(session, jid: str) -> str | None:
    """Which property this chat belongs to (D13) — **a scoped read**.

    `telegram.chat_links` is a `Configuration` row, so this can only run once the account is
    bound. The map is per-account by construction, which is what keeps `property_slug` and
    `account` orthogonal (N5): the account says whose estate, the chat says which house.

    Falls back to `resolve_default_property`, which returns the sole property's slug when an
    account has exactly one and `None` otherwise (L2). That fallback is safe *here* in a way it
    would never be at the account level: the account is already established by
    `resolve_sender`, so this only ever chooses between houses the sender genuinely belongs to.
    """
    import json

    from mihomes.services.config_service import get_config
    from mihomes.services.gateways.review_common import resolve_default_property

    raw = get_config(session, "telegram.chat_links") or "{}"
    try:
        links = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        links = {}

    slug = links.get(str(jid)) if isinstance(links, dict) else None
    return slug or resolve_default_property(session)


def _reply(chat_id: str, text: str) -> None:
    """Send one message back over the transport, outside any account scope.

    Separate from `_dispatch` because it is the *unlinked* path's only action: there is no
    account to open a session for, so this cannot go through `GatewayAdapter`, which the
    responders build inside a scoped session.
    """
    try:
        from mihomes.services.gateways.telegram.client import TelegramClient

        TelegramClient().send_message(chat_id, text)
    except Exception:
        # A failed reply must not turn into a 500: Telegram would retry the update, and the
        # retry would fail the same way. The sender sees silence, which is the milder failure.
        logger.exception("telegram webhook: could not send reply to %s", chat_id)


def _dispatch(resolved, message: dict) -> None:
    """Open a session bound to the resolved account and hand off (steps 4–5).

    **A seam, deliberately.** Everything account-specific happens inside `account_context`, so
    the dedup store, `is_trusted_sender` and `dispatch_items` all see the tenant the sender
    resolved to — and `dispatch_items` re-asserts that agreement itself (A11), so a bug here
    fails loudly rather than writing into the wrong estate.
    """
    from mihomes.db import get_session
    from mihomes.services.gateways.dedup import ProcessedIdStore
    from mihomes.services.gateways.telegram.extractor import (
        MAX_PROCESSED_IDS,
        PROCESSED_IDS_KEY,
    )
    from mihomes.tenancy.context import account_context

    with account_context(resolved.account_id):
        with get_session() as session:
            # A16 (redelivery) and **A17 (no double transport)** are the same store, and that
            # is the entire mechanism: the webhook and the poller must key on the *same*
            # config key, or whichever sees an update first is invisible to the other.
            #
            # An earlier version of this line used its own key, `gateway.telegram.processed` —
            # which would have been a fifth disjoint store and precisely the M22 defect
            # `dedup.py`'s docstring says it exists to fix ("each gateway had FOUR disjoint
            # processed-id stores ... so an id handled by one poller was invisible to the other
            # and messages were double-processed into duplicate issues/tasks").
            #
            # `PROCESSED_IDS_KEY` is imported rather than retyped so the two transports cannot
            # drift apart in a later edit.
            store = ProcessedIdStore(PROCESSED_IDS_KEY, cap=MAX_PROCESSED_IDS)
            update_id = message.get("id") or ""
            if update_id and store.contains(update_id):
                logger.info("telegram webhook: update %s already processed", update_id)
                return

            from mihomes.services.gateways.telegram.responder import process_and_respond

            # **Resolve the chat→property map here**, inside the scope: it lives in
            # `telegram.chat_links`, a `Configuration` row, which is tenant-owned and therefore
            # unreadable until the account is bound. That ordering is why `normalize_*` leaves
            # `propertySlug` as None rather than filling it.
            #
            # Load-bearing, not cosmetic: `responder.py:208` drops any message with neither a
            # `propertySlug` nor a `property_slug` argument, so without this the whole webhook
            # path returns "No linked chat found" and writes nothing. Measured — see the
            # envelope comment in `gateways/webhook.py`.
            slug = _property_for_chat(session, message["jid"])
            process_and_respond(session, [message], property_slug=slug)

            if update_id:
                store.add([update_id])


__all__ = ["LINK_PROMPT", "router", "telegram_webhook"]
