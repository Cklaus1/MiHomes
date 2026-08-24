"""`POST /webhooks/stripe` — the only route in the app with no session and no tenant scope.

**Every other route is authorised by who is asking. This one is authorised by what was sent.**
Stripe is not a user: there is no cookie, no principal, no account, and no role. Authentication is
a signature over the raw request body (N3), and that difference drives every unusual thing in this
module.

## Why it declares no action

`test_route_declarations.py` requires every endpoint to declare a matrix action, with two
allowlists. The temporary one (`UNDECLARED_MODULES`) is empty and its ceiling is pinned at 0, so
adding this module there would break `test_ceiling_is_not_slack` — correctly, because that list
means *"not declared yet"* and this route will never be declared. It goes in
`PERMANENT_ALLOWLIST`, which is for decisions, alongside `auth`: that module is excused because
identity does not yet exist, this one because identity is **irrelevant**.

## Why the raw body, and why nothing is parsed first

N3: *"Do not parse the webhook body before verifying the signature."* Verification is over the
exact bytes Stripe signed. Any framework that hands you a parsed body has already re-serialized
it — key order, whitespace and unicode escaping all change — so the signature either fails to
match, or (worse) matches after you have already acted on unverified input. `await request.body()`
is the only read that happens before verification.

## Why it always returns 200 to Stripe, even on failure

Stripe retries any non-2xx with backoff for days. That is correct for a transient failure and
actively harmful for a permanent one: an event we can never process — a customer we cannot
resolve, a malformed payload — would be redelivered indefinitely. So the handler distinguishes
them:

- **bad signature → 400.** Not a retry candidate; it is either an attack or a misconfigured
  endpoint secret, and both need to be visible rather than absorbed.
- **anything we processed or deliberately ignored → 200**, including events we could not map to
  an account. The ledger records them (with `error` set) so they are auditable without being
  retried forever.
- **an unexpected server error → 500**, so Stripe *does* retry. This is the one case where the
  retry is what we want, and idempotency (Step 5) is what makes it safe.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from mihomes.services.billing.provider import (
    BillingProviderError,
    WebhookVerificationError,
    get_billing_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: The path prefix exempt from the Host and Origin guards — see `web/security.py`.
#:
#: Declared here, next to the route it describes, and imported by the middleware so the two
#: cannot drift: a path renamed in the decorator below without updating this constant would
#: silently re-arm the guards and 400 every live webhook.
WEBHOOK_PATH_PREFIX = "/webhooks/"

STRIPE_SIGNATURE_HEADER = "stripe-signature"


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request) -> Response:
    """Verify, then hand off. **No `Depends(get_db)`** — see below.

    The session is opened *inside* the handler rather than injected, because the injected
    `get_db` binds tenant context from the request's principal and there is no principal here.
    Opening it explicitly keeps that asymmetry visible at the one place it applies.
    """
    signature = request.headers.get(STRIPE_SIGNATURE_HEADER, "")
    if not signature:
        # No signature at all is not a verification *failure* — there is nothing to verify. It
        # means something that is not Stripe posted here, so say so plainly rather than letting
        # the adapter raise on an empty string.
        return Response(content="Missing signature header.", status_code=400)

    raw_body = await request.body()

    try:
        provider = get_billing_provider("stripe")
        event = provider.handle_webhook_event(payload=raw_body, signature=signature)
    except WebhookVerificationError:
        # Deliberately not logged with the body or the signature: both are attacker-controlled on
        # a failed verification, and a log line is a place where untrusted bytes get read later.
        logger.warning("stripe webhook: signature verification failed")
        return Response(content="Invalid signature.", status_code=400)
    except BillingProviderError:
        logger.exception("stripe webhook: provider error before dispatch")
        return Response(content="Billing provider unavailable.", status_code=500)

    if event is None:
        # A type we deliberately ignore (`_EVENT_TYPE_MAP` miss), or an event with no customer.
        # Ack so Stripe stops sending it.
        return Response(status_code=200)

    _dispatch(event)
    return Response(status_code=200)


def _dispatch(event) -> None:
    """Hand the verified event to `BillingService`.

    **A seam, deliberately, and it is a stub until Step 5.** The whole idempotency and
    out-of-order sequence lives in the service (§5.2), not in the route: putting it here would
    mean the reconciliation sweep — which applies the same state through a different entry point
    — could not share it, and two implementations of "apply this subscription state" is exactly
    the drift D2 separates the layers to prevent.
    """
    from mihomes.db import get_session
    from mihomes.services.billing.service import handle_verified_event

    with get_session() as session:
        handle_verified_event(session, event)


__all__ = ["WEBHOOK_PATH_PREFIX", "router", "stripe_webhook"]
