"""Cancel a paid plan (downgrade to Free), and undo it — the owner's button on /billing.

Two shapes, decided by whether the account pays through Stripe:

* **Paying** (`stripe_subscription_id`): Stripe is told to cancel **at period end** (`PRICING`
  §4.4 — "reverts to Free at period end, not instantly"). The customer keeps what they paid for;
  Stripe keeps the subscription `active` until then and sends `customer.subscription.deleted`
  when it ends, which `apply_subscription_state` turns into `canceled` → effective Free. The
  local `cancel_at_period_end` flag is set straight away so the page can say so without waiting
  for the webhook. **Undo** before the period ends asks Stripe to keep renewing.
* **Not paying** (a no-card trial, a hand-set plan): there is nothing to wait for, so the account
  drops to Free now — the same field set as trial expiry.

**Nothing is deleted either way.** Homes past Free's limit become read-only (§4.3), and
`downgrade_consequences` says so before the owner confirms.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mihomes.entitlements.limits import PLAN_LIMITS, effective_plan
from mihomes.models.account import Account

logger = logging.getLogger(__name__)


class NothingToCancel(Exception):
    """The account is already on Free (or already cancelling)."""


#: Stripe statuses after which the subscription can no longer be modified or resumed.
_ENDED = frozenset({"canceled", "incomplete", "incomplete_expired"})


def has_live_subscription(account) -> bool:
    """A Stripe subscription that still exists and can be changed — not merely a stored id.

    `stripe_subscription_id` is never cleared when a subscription ends, so the id alone would
    call a `canceled` account "paying": the page would offer the portal instead of checkout and
    a "Keep Pro" that asks Stripe to modify a deleted subscription.
    """
    return bool(getattr(account, "stripe_subscription_id", None)) and (
        getattr(account, "subscription_status", None) not in _ENDED
    )


def downgrade_consequences(session: Session, account: Account) -> dict:
    """What dropping to Free would do, in numbers, for the confirm step.

    Counted, not guessed: an owner with four homes must see "3 homes become read-only" before
    pressing the button, not discover it afterwards.
    """
    from mihomes.models.property import Property

    homes = session.execute(
        select(func.count()).select_from(Property).where(Property.account_id == account.id)
    ).scalar_one()
    free = PLAN_LIMITS["free"]
    return {
        "homes": homes,
        "free_homes": free["max_homes"],
        "frozen_homes": max(0, homes - free["max_homes"]),
        "free_seats": free["max_seats"],
        "at_period_end": has_live_subscription(account),
        "period_end": account.current_period_end,
    }


def cancel_plan(session: Session, account: Account, *, provider=None) -> str:
    """Cancel to Free. Returns `"at_period_end"` or `"now"`."""
    if effective_plan(account.plan, account.subscription_status) == "free":
        raise NothingToCancel("already on Free")

    if has_live_subscription(account):
        if account.cancel_at_period_end:
            raise NothingToCancel("already cancelling at period end")
        billing = provider or _provider()
        billing.cancel(subscription_id=account.stripe_subscription_id, at_period_end=True)
        account.cancel_at_period_end = True
        session.commit()
        logger.info("cancel at period end requested for account %s", account.id)
        return "at_period_end"

    # No subscription: a trial or a hand-set plan. Same fields as `_expire_trial`, and
    # `trial_used_at` is kept — cancelling a trial does not earn a second one.
    account.plan = "free"
    account.subscription_status = None
    account.trial_ends_at = None
    account.cancel_at_period_end = False
    session.commit()
    logger.info("downgraded to free immediately for account %s", account.id)
    return "now"


def resume_plan(session: Session, account: Account, *, provider=None) -> None:
    """Undo a pending cancel — the subscription keeps renewing."""
    if not (has_live_subscription(account) and account.cancel_at_period_end):
        raise NothingToCancel("no pending cancellation")
    billing = provider or _provider()
    billing.resume(subscription_id=account.stripe_subscription_id)
    account.cancel_at_period_end = False
    session.commit()
    logger.info("cancellation undone for account %s", account.id)


def _provider():
    from mihomes.services.billing.provider import get_billing_provider

    return get_billing_provider("stripe")
