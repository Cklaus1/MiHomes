"""One-click unsubscribe — RFC 8058 (SPEC-005 §6 Step 9, B4/N9/N10).

`SAAS_PRD:167` gives "email opt-out" three words in an NFR table and no design anywhere. B4 is
the requirement written properly: a one-click POST, a suppression list, and the
transactional/lifecycle distinction that makes opt-out legally coherent (D13).

## One click means one click (N10)

The `POST` **completes** the unsubscribe. There is no confirmation page, and adding one would not
be caution — mailbox providers that find the `List-Unsubscribe-Post` header leads to a form treat
the header as broken, which costs deliverability. The mechanism intended to honour opt-outs would
make the sender look worse at honouring them.

The `GET` exists for the human-visible footer link and is deliberately *not* one-click: a browser
prefetching a link in an email must not unsubscribe anyone, and prefetchers issue GETs. So the GET
renders a page with a form that POSTs to the same address.

## The token is an HMAC, never the address (N9)

`?email=someone@example.com` lets anyone unsubscribe anyone, and addresses are enumerable.
`suppression.unsubscribe_token` signs the normalized address with the app secret — self-
authenticating, stateless, no token table to expire.

## Two allowlist entries, and the reason is not the webhook's

This module joins `PERMANENT_ALLOWLIST` because a mail client is not a user: there is no cookie,
no principal, and no account on the request. `ALLOWLIST_MECHANISMS` then requires it to name what
authenticates instead — the signed token above.

**And it needs its own Host/Origin exemption.** Measured: a POST to `/unsubscribe` with
`Host: mihomes.ai` returns **400 Invalid Host** today, while `/webhooks/...` passes because it
matches `WEBHOOK_PATH_PREFIX`. The guards assume a browser the user is driving; a mail client
POSTing from its own infrastructure to the public hostname is neither, exactly as Stripe is not.
"""

from __future__ import annotations

import html
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy.orm import Session

from mihomes.services.email.suppression import (
    InvalidUnsubscribeToken,
    suppress,
    verify_unsubscribe_token,
)
from mihomes.web.deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

#: Both guards skip this prefix. Lives beside the route, like `WEBHOOK_PATH_PREFIX`, so a rename
#: cannot silently re-arm them and start 400-ing every unsubscribe in production.
UNSUBSCRIBE_PATH_PREFIX = "/unsubscribe"


@router.post("/unsubscribe")
def one_click_unsubscribe(
    request: Request,
    email: str = Form(...),
    token: str = Form(...),
    db: Session = Depends(get_db),
):
    """**RFC 8058** — the POST completes the unsubscribe. No confirmation, no redirect (N10).

    Returns 200 on an invalid token as well as a valid one, deliberately. A 403 would tell a
    caller which addresses are on file, and the only party who can act on the difference is
    someone probing — the legitimate mail client cannot fix a bad token either way.

    Idempotent: `suppress` returns `None` when the address was already suppressed, and a second
    click from a re-opened email is an ordinary case rather than an error.
    """
    try:
        verify_unsubscribe_token(email, token)
    except InvalidUnsubscribeToken:
        logger.warning("unsubscribe: token did not verify")
        return PlainTextResponse("Unsubscribed.", status_code=200)

    # `get_db` yields a plain session with no principal dependency, which is what an
    # allowlisted route needs: taking one that assumed authentication is how such a route
    # acquires an accidental 401.
    row = suppress(db, email, reason="unsubscribe")
    db.commit()

    logger.info("unsubscribed: newly=%s", row is not None)
    return PlainTextResponse("Unsubscribed.", status_code=200)


@router.get("/unsubscribe")
def unsubscribe_form(request: Request, email: str = "", token: str = ""):
    """The footer link's landing page — a form, **not** an action.

    A GET must never unsubscribe: mail clients and security scanners prefetch links, and a
    prefetched GET that suppressed the address would opt people out who never clicked anything.
    RFC 8058's one-click path is the POST above; this is the human one.

    **Both parameters are escaped.** They arrive in a query string on an unauthenticated route
    and land in HTML attributes — the textbook stored-reflection shape, and the fact that a
    legitimate caller only ever sends an address and a hex digest says nothing about what an
    attacker sends. `html.escape(quote=True)` because the values sit inside `value='...'`.
    """
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>Unsubscribe</title>"
        "<h1>Unsubscribe</h1>"
        "<p>Stop receiving updates and reminders at this address? "
        "Receipts and account notices will still be sent.</p>"
        "<form method='post' action='/unsubscribe'>"
        f"<input type='hidden' name='email' value='{html.escape(email, quote=True)}'>"
        f"<input type='hidden' name='token' value='{html.escape(token, quote=True)}'>"
        "<button type='submit'>Unsubscribe</button></form>"
    )
