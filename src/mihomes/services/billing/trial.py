"""The app-managed trial — 14 days of Pro, no card, one per account ever (F3, D4).

**There is no Stripe object during a trial**, and that single fact drives everything here.
`PRICING` §4.2: *"with no card there is **no Stripe subscription during the trial** — the trial is
app-managed state (`plan=pro`, `subscription_status=trialing`, `trial_ends_at`), and the
entitlements service treats it as `trialing`."*

Three consequences, each load-bearing somewhere else in the phase:

- **The `trial_will_end` webhook never fires**, so `cli/jobs.py::trial_sweep` is the trial's only
  clock (F3). The handler for that event is written and unreachable — deliberate room for a
  card-first trial later, listed in §7's deferred table, not dead code.
- **Entitlements need no special case.** `_STATUS_TO_EFFECTIVE_PLAN` already maps `trialing` to
  *"the account's own plan"*, so setting `plan="pro"` gives real Pro limits through the ordinary
  path. A trial-shaped branch in `can()` would have been a second mechanism answering a question
  the first one already answers.
- **Nothing here touches `stripe_customer_id`.** A trial that created a Stripe Customer would make
  `start_checkout` reuse it at conversion, which is right — but creating one *before* the user has
  agreed to pay is a vendor record for someone who may never convert, and D4 says Stripe objects
  are created at conversion only.

## Started on first gated action, not at signup

§4.2: *"a trial that starts at signup is often burned before the 2nd home or first staff hire
appears."* So `maybe_start_trial` is called from the gate, with the `Denied` in hand — the moment
the user wants the thing the trial would give them.

## One trial per account, ever

`trial_used_at` is the flag, and it is **never cleared** — not on expiry (`jobs._expire_trial`
already asserts this), not on conversion, not on cancellation. It records history, not state.
Clearing it anywhere would hand every account an unlimited supply of trials, which is the quietest
way to give the product away.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

__all__ = ["TRIAL_DAYS", "TRIAL_PLAN", "is_on_trial", "maybe_start_trial", "start_trial"]

#: `PRICING` §4.2 — *"14-day Pro trial (PLACEHOLDER length)"*.
#:
#: Tagged PLACEHOLDER in the source, so it is O1's to set at launch. A literal rather than an env
#: var for the same reason the plan limits are (C11): trial length is product definition, already
#: committed, and moving it to the environment would make it invisible to anyone reading the
#: policy.
TRIAL_DAYS = 14

#: The plan a trial grants. Pro, not Estate: §4.2 sells a *Pro* trial, and granting the top tier
#: would make the trial a worse predictor of what the customer is actually buying.
TRIAL_PLAN = "pro"


def is_on_trial(account, now: datetime | None = None) -> bool:
    """Is this account inside an active trial?

    Both conditions, deliberately. `subscription_status == "trialing"` alone would keep granting
    Pro after `trial_ends_at` passed and before the nightly sweep ran — a window of up to a day in
    which the trial is over and the entitlements do not know it. Checking the date here makes the
    sweep the thing that *tidies up*, not the thing that enforces.
    """
    if getattr(account, "subscription_status", None) != "trialing":
        return False
    ends_at = getattr(account, "trial_ends_at", None)
    return ends_at is not None and ends_at > (now or datetime.now(UTC))


def start_trial(session: Session, account, *, now: datetime | None = None) -> bool:
    """Start the trial. Returns `False` if this account has already used one.

    **Refuses on `trial_used_at`, not on the current plan** (A18). A user who trials, converts to
    Pro, and later cancels back to Free must not get a second trial — and a plan check would give
    them one, because by then they look exactly like a new Free account.

    Idempotent for a trial already running: returns `False` rather than extending the end date,
    so a double-click on an upgrade prompt cannot buy another fortnight.
    """
    if getattr(account, "trial_used_at", None) is not None:
        return False

    now = now or datetime.now(UTC)
    account.plan = TRIAL_PLAN
    account.subscription_status = "trialing"
    account.trial_ends_at = now + timedelta(days=TRIAL_DAYS)
    account.trial_used_at = now
    session.commit()

    logger.info(
        "trial started for account %s: %s until %s",
        account.id, TRIAL_PLAN, account.trial_ends_at.date(),
    )
    return True


def maybe_start_trial(session: Session, account, *, action: str) -> bool:
    """Start a trial **because the user just hit a gate** (§4.2's "first gated action").

    Called with the action that was denied, so the trial begins at the moment of intent rather
    than at signup — *"a trial that starts at signup is often burned before the 2nd home or first
    staff hire appears."*

    Returns whether a trial was started, so the caller can retry the action it just refused.
    **Deliberately not retrying it here**: this module knows nothing about homes or seats, and a
    trial service that re-invoked arbitrary callers would be a second control-flow path through
    every gate in the app.
    """
    if getattr(account, "trial_used_at", None) is not None:
        return False
    if getattr(account, "stripe_subscription_id", None):
        # Already a paying customer — a gate they hit is a real limit on a plan they bought, not
        # an invitation to trial. Reaching here would mean an Estate customer at their seat cap
        # being handed Pro, which is a downgrade dressed as a gift.
        return False

    started = start_trial(session, account)
    if started:
        logger.info("trial started from gated action %r on account %s", action, account.id)
    return started
