"""Billing routes — checkout, portal, and the plan page. **Owner-only** (D8).

`ONBOARDING` §9.2 row 15 is `billing.manage`, `(owner=ALLOW, admin=DENY, staff=DENY)`,
`Access.ACCOUNT`. **SPEC-003 already shipped that key**, which is why this module declares an
existing action rather than adding one — and adding a 21st would have broken A1, which asserts the
matrix covers rows 1–20 exactly.

D8's reasoning, worth restating because "admin" usually means "can do everything": *"admins manage
the estate, not the card."* An admin runs the household — properties, staff, work orders — and that
is a different trust boundary from the payment method. A house manager should not be able to
upgrade the plan, downgrade it, or open a portal that can cancel the subscription.

**Enforcement is not in this file.** `enforce_declared_action` reads each route's `@declares` and
applies the role gate app-wide, so a non-owner is refused before any handler runs. That is why
there is no role check in the bodies below — a second one here would be a second place to get it
wrong, and the first place would still be the one that matters.

## N1 is the reason `/billing/success` grants nothing

The redirect target after Checkout is a **confirmation page, not an entitlement change**. The user
controls that URL: they can reach it without paying, replay it, or never arrive at all after a
successful payment. Only a signature-verified webhook changes state (D1). SPEC-004 calls this
*"the single most common Stripe integration defect"*, and it fails **open** — which is why the
success handler here reads the account and renders it, and does not write.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.models.account import Account
from mihomes.services.billing import service as billing_service
from mihomes.services.billing.prices import PriceConfigurationError
from mihomes.services.billing.provider import BillingProviderError
from mihomes.web.deps import get_db, require_authenticated, templates

logger = logging.getLogger(__name__)

router = APIRouter()

BILLING_ACTION = "billing.manage"

#: The plans a client may ask to buy, and the cadences they may ask for.
#:
#: **Validated server-side against this set, never trusted from the form** — the client-supplied
#: half of D3/N2. A price id can never arrive (the interface has no parameter for one), but a
#: *plan name* can, and an unvalidated one would reach `price_id_for` and either raise a confusing
#: configuration error or, worse, resolve to a price the caller chose rather than the one the
#: product sells. `"free"` is deliberately absent: Free has no Stripe object (D4).
SELLABLE_PLANS = frozenset({"pro", "estate"})
SELLABLE_INTERVALS = frozenset({"monthly", "annual"})


def _account(db: Session, principal) -> Account:
    return db.get(Account, principal.account_id)


@router.get("/billing")
@declares(BILLING_ACTION, Access.ACCOUNT)
def index(request: Request, principal=require_authenticated(),
          db: Session = Depends(get_db)):
    """The plan page: current plan, status, and what upgrading would buy."""
    account = _account(db, principal)
    return templates.TemplateResponse(request, "billing.html", _page_context(account))


def _page_context(account: Account, **extra) -> dict:
    from mihomes.entitlements.limits import effective_plan
    from mihomes.services.billing.plans import plan_cards

    return {
        "page": "billing",
        "account": account,
        "current_plan": effective_plan(account.plan, account.subscription_status),
        "plans": plan_cards(account),
        **extra,
    }


@router.post("/billing/checkout")
@declares(BILLING_ACTION, Access.ACCOUNT)
def checkout(
    request: Request,
    plan: str = Form(...),
    interval: str = Form("monthly"),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """Start a subscription purchase and redirect to Stripe's hosted Checkout.

    Returns a 303 to Stripe rather than rendering anything: the payment form is Stripe's, which is
    what keeps card data out of this application entirely.
    """
    if plan not in SELLABLE_PLANS or interval not in SELLABLE_INTERVALS:
        # Not a user-facing error path — the form only offers valid combinations, so reaching
        # here means a hand-crafted request. Refuse plainly rather than passing it downstream.
        return _error_page(request, db, principal, "That plan is not available.", 400)

    base = str(request.base_url).rstrip("/")
    try:
        url = billing_service.start_checkout(
            db,
            _account(db, principal),
            plan=plan,
            interval=interval,
            success_url=f"{base}/billing/success",
            cancel_url=f"{base}/billing",
        )
    except PriceConfigurationError:
        # O1 — the Stripe Products and `STRIPE_PRICE_*` vars are not set up yet. A deployment
        # problem, not the user's, and the log carries the variable name.
        logger.exception("checkout blocked: price configuration incomplete")
        return _error_page(
            request, db, principal,
            "Billing is not fully configured yet. Please try again later.", 503,
        )
    except BillingProviderError:
        logger.exception("checkout failed at the billing provider")
        return _error_page(
            request, db, principal,
            "We could not reach the payment provider. Please try again.", 502,
        )

    return RedirectResponse(url, status_code=303)


@router.post("/billing/portal")
@declares(BILLING_ACTION, Access.ACCOUNT)
def portal(request: Request, principal=require_authenticated(),
           db: Session = Depends(get_db)):
    """Open the Stripe Customer Portal — plan changes, payment method, cancellation.

    `BILLING` §7: this is why there is no custom billing UI to build or maintain. Cancellation in
    particular arrives back as `customer.subscription.deleted`, so the entitlement change still
    travels the webhook path (D1) rather than being applied here.
    """
    base = str(request.base_url).rstrip("/")
    try:
        url = billing_service.start_portal_session(
            _account(db, principal), return_url=f"{base}/billing",
        )
    except BillingProviderError:
        logger.exception("portal session failed")
        return _error_page(
            request, db, principal,
            "There is no billing account to manage yet.", 400,
        )
    return RedirectResponse(url, status_code=303)


@router.get("/billing/success")
@declares(BILLING_ACTION, Access.ACCOUNT)
def success(request: Request, principal=require_authenticated(),
            db: Session = Depends(get_db)):
    """Confirmation only. **Grants nothing** — N1.

    The account is re-read so the page shows whatever the webhook has already applied, which is
    usually everything by the time the browser gets back. When it is not, the page says the
    upgrade is being confirmed rather than pretending it has not happened; polling or a refresh
    resolves it. What it must never do is write.
    """
    account = _account(db, principal)
    return templates.TemplateResponse(
        request,
        "billing.html",
        _page_context(
            account,
            notice=(
                "Payment received. Your plan updates as soon as we get confirmation from the "
                "payment provider — refresh in a moment if it is not shown yet."
            ),
        ),
    )


def _error_page(request: Request, db: Session, principal, message: str, status: int):
    """Render the plan page carrying an error, rather than a bare status.

    A failed upgrade is one of the worst places to show a stack trace or an empty page: the user
    has just tried to give the product money and does not know whether it worked.
    """
    return templates.TemplateResponse(
        request,
        "billing.html",
        _page_context(_account(db, principal), error=message),
        status_code=status,
    )


@router.post("/billing/trial")
@declares(BILLING_ACTION, Access.ACCOUNT)
def start_trial(
    request: Request,
    action: str = Form(""),
    next: str = Form(""),
    principal=require_authenticated(),
    db: Session = Depends(get_db),
):
    """The upgrade prompt's **Start 14-day Pro trial** button — `PRICING` §4.1's one-click start.

    The gates refuse and show the prompt; this is the only thing that starts a trial, so a trial
    is always something the owner chose. `maybe_start_trial` enforces one trial per account and
    none for a paying customer, so a replayed or hand-crafted POST can do nothing a real click
    could not.

    On success, back to what they were trying to do (`next`, through `safe_next` so this cannot be
    an open redirect). If no trial was available — already used, or already paying — to the plan
    page, which is the remaining way forward.
    """
    from mihomes.auth.session_flow import safe_next
    from mihomes.services.billing.trial import maybe_start_trial

    if maybe_start_trial(db, _account(db, principal), action=action or "upgrade_prompt"):
        return RedirectResponse(safe_next(next) or "/", status_code=303)
    return RedirectResponse("/billing", status_code=303)


@router.get("/trial-status", response_class=HTMLResponse)
@declares("property.view", Access.COLLECTION)
def trial_status(request: Request, principal=require_authenticated(),
                 db: Session = Depends(get_db)):
    """The trial banner, as a fragment `base.html` fetches after the page has loaded.

    **Not under `/billing/`**: every `/billing` route is owner-only (row 15) and a test holds
    that line, but the banner is for everyone in the account — a housekeeper should know the
    plan's Pro features lapse in three days as much as the owner should.

    **Its own request on purpose.** The first banner (3eb0dd4, reverted in 6628d39) read the
    account inside `resolve_principal` on *every* request and swallowed its errors; one failure
    left a transaction idle-in-transaction holding locks, and every later request blocked on the
    session update — the whole app hung. Here the read runs in this route's own `get_db`
    transaction with nothing swallowed: if it fails, this fragment is empty and the page around
    it is unaffected.

    Keyed on `subscription_status == "trialing"` rather than `is_on_trial()`, so the window
    between `trial_ends_at` and the nightly sweep says "ended" instead of saying nothing while
    the account still has Pro.
    """
    from datetime import UTC, datetime
    from math import ceil

    account = _account(db, principal)
    trial = None
    if account is not None and account.subscription_status == "trialing" and account.trial_ends_at:
        ends_at = account.trial_ends_at
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=UTC)
        seconds_left = (ends_at - datetime.now(UTC)).total_seconds()
        trial = {
            "plan": account.plan,
            "days_left": max(0, ceil(seconds_left / 86400)),
            "expired": seconds_left <= 0,
        }
    return templates.TemplateResponse(
        request,
        "partials/trial_banner.html",
        # The plans link only for the owner — for anyone else it would be a 403 (row 15).
        {"trial": trial, "is_owner": principal.role == "owner"},
    )
