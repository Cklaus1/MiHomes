"""`BillingService` — everything the adapter must not do: the DB, account mapping, idempotency.

The seam (D2/N5): **adapter = vendor I/O + normalization; service = state + business rules.**
`NormalizedEvent` arrives carrying Stripe's customer id and no `account_id`, and resolving that is
this module's job.

## The order of §5.2's five steps is the design, not a suggestion

    1. verify            (the route's job, already done when we are called)
    2. INSERT the ledger row — a unique violation means already-processed, return
    3. map provider_customer_id -> Account; unknown -> record and return
    4. drop if occurred_at predates the state already applied
    5. apply the BILLING §5 status mapping

**Insert *first*, before the mapping and before any state change** (N4). The obvious
implementation — `SELECT` to check, then `INSERT` — races: two concurrent deliveries of one event
both see "not present" and both proceed. Stripe retries on any non-2xx and redelivers
concurrently under load, so this is ordinary traffic rather than a pathological case. The unique
constraint is the only thing that can arbitrate, and it can only do so if the insert happens
before the work.

**Why the ledger row is committed in its own transaction.** Steps 2–5 in one transaction reads
well and is wrong: a rollback anywhere in 3–5 would take the ledger row with it, and the retry
that follows would find no record and reprocess. Recording that we *saw* an event and recording
what we *did* with it are different facts with different lifetimes — the first must survive the
second failing, which is what `error` is for.

## Out-of-order is a real Stripe property, not a hypothetical

`BILLING` §6: *"Stripe does **not** guarantee event ordering. Never apply an older state over a
newer one."* The practical shape is a retry: an event emitted at T1 fails, is retried at T3, and
arrives after the T2 event that superseded it. Applying it would resurrect a cancelled
subscription or undo an upgrade — and the account would look correct to everyone except the
customer.

`accounts` has no "last applied" column, and adding one would be an `accounts` migration, which
N13 forbids. So the ledger answers it: the most recent successfully-applied event for this
account **is** the last-applied timestamp. The table exists, it is already indexed by account, and
it cannot drift from the thing it describes.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mihomes.models.account import Account
from mihomes.models.processed_webhook_event import ProcessedWebhookEvent
from mihomes.services.billing.provider import NormalizedEvent, SubscriptionState

logger = logging.getLogger(__name__)

__all__ = [
    "apply_subscription_state",
    "handle_verified_event",
    "start_checkout",
    "start_portal_session",
]

PROVIDER = "stripe"

#: Event types that carry authoritative subscription state (`BILLING` §6).
#:
#: Only these are subject to the out-of-order check. An `invoice.paid` arriving late is not
#: dangerous — it changes no plan — while a stale `subscription.updated` is exactly the event that
#: would resurrect a cancelled plan.
_STATE_BEARING = frozenset({
    "subscription.activated",
    "subscription.updated",
    "subscription.cancelled",
})


def handle_verified_event(session: Session, event: NormalizedEvent) -> None:
    """Apply a **signature-verified** event, exactly once.

    The name says `verified` because that precondition is the route's to establish and this
    function's to assume — a caller handing over an unverified event is the defect N3 describes,
    and naming the parameter is the cheapest guard against it.

    Returns silently in every already-handled case (duplicate, unknown customer, stale). The
    route acks 2xx regardless: Stripe must stop redelivering an event that has been dealt with,
    and "dealt with" includes "deliberately ignored".
    """
    ledger_row = _record_event(session, event)
    if ledger_row is None:
        logger.info("stripe webhook: duplicate event %s ignored", event.raw_event_id)
        return

    account = _resolve_account(session, event.provider_customer_id)
    if account is None:
        # Recorded, not retried. An event for a customer we cannot place — a Stripe account
        # shared with another environment, a test-mode event reaching a live endpoint, an
        # account deleted on our side — would otherwise be redelivered for days.
        _mark_error(session, ledger_row, "no account for provider_customer_id")
        logger.warning(
            "stripe webhook: no account for customer %s (event %s)",
            event.provider_customer_id, event.raw_event_id,
        )
        return

    ledger_row.account_id = account.id
    session.commit()

    if _is_stale(session, event, account):
        _mark_error(session, ledger_row, "superseded by a newer applied event")
        logger.info(
            "stripe webhook: dropping stale %s for account %s (occurred_at %s)",
            event.type, account.id, event.occurred_at,
        )
        return

    if event.subscription is not None:
        apply_subscription_state(session, account, event.subscription)

    _apply_dunning(session, account, event)

    logger.info(
        "stripe webhook applied: type=%s event=%s account=%s",
        event.type, event.raw_event_id, account.id,
    )


#: Events that end a dunning sequence.
#:
#: * `invoice.paid` — the direct signal.
#: * `customer.subscription.updated` — the indirect one: a customer who fixed their card in
#:   Stripe's portal produces this without necessarily producing an `invoice.paid` in the same
#:   delivery. Only ends the sequence when the status has actually left the failure state.
#: * `subscription.cancelled` — **Step 10's third clause**: *"the ladder never outlives the
#:   subscription that started it."* A customer who cancels mid-ladder has ended the
#:   relationship; two more weeks of "update your card" is dunning someone who is no longer a
#:   customer. No §8 criterion covers this clause (A23 is the schedule, A24 is recovery), so it
#:   would have shipped unbuilt with F.3a green — see harness §2.2 D16.
RECOVERY_EVENT_TYPES = frozenset(
    {"invoice.paid", "customer.subscription.updated", "subscription.cancelled"}
)


def _apply_dunning(session: Session, account: Account, event: NormalizedEvent) -> None:
    """Start the ladder on a failed payment; cancel it on recovery (SPEC-005 Step 10).

    **Enqueued, never sent inline** (N2). A slow provider must not become a slow webhook: Stripe
    times out and redelivers, and a redelivered `payment_failed` that the ledger has already
    recorded is dropped — so the emails would be lost precisely when the mail path is unwell.

    Failures here are logged and swallowed. The billing *state* has already been applied and
    committed above; letting a mail problem raise would fail the webhook, cost the ack, and put
    the account's plan and Stripe's view of it out of step over an email.
    """
    from mihomes.services.billing.dunning import cancel_ladder, start_ladder

    try:
        if event.type == "invoice.payment_failed":
            # `_billing_email` RAISES on an account with no active owner rather than returning
            # None — that is a corrupted-state signal, not a branch. Caught by the wrapper
            # below along with everything else, which is the right handling here: an account
            # that cannot be emailed still had its billing state applied.
            email = _billing_email(session, account)
            start_ladder(
                session,
                to=email,
                account_id=account.id,
                plan=account.plan,
                billing_url=_billing_url(),
            )
            session.commit()
            return

        if event.type not in RECOVERY_EVENT_TYPES:
            return

        # A subscription still in `past_due` has not recovered — the customer is mid-grace and
        # the remaining rungs are exactly what should still fire. Only a status that has left
        # the failure state cancels.
        status = getattr(event.subscription, "status", None)
        if event.type == "customer.subscription.updated" and status in (
            "past_due",
            "unpaid",
            None,
        ):
            return

        if cancel_ladder(session, account.id):
            session.commit()
    except Exception:
        logger.exception("dunning: could not update the ladder for account %s", account.id)


def _billing_url() -> str:
    """Where a dunning email sends the customer. The billing page, not Stripe's portal.

    The portal needs a session minted per visit, and a link that has expired by the time someone
    opens the email is worse than one more click.
    """
    import os

    base = os.environ.get("MIHOMES_BASE_URL", "https://app.mihomes.ai").rstrip("/")
    return f"{base}/billing"


def apply_subscription_state(
    session: Session, account: Account, state: SubscriptionState
) -> bool:
    """**The single place `plan` / `subscription_status` / `current_period_end` are written.**

    SPEC-002 §4.2 requires it in as many words — those columns are *"written ONLY by the billing
    webhook handler"* — and both the live webhook path and the reconciliation sweep (Step 18)
    call through here. That shared call is the point: the sweep exists to correct drift after a
    dropped webhook, so if it applied state by its own route the two would eventually disagree
    about what a customer bought, and the disagreement would surface as a plan that flips back
    and forth depending on which path ran last.

    Returns whether anything actually changed, which `reconcile` reports as "drift corrected".

    ## The plan and the status are two different questions

    `BILLING` §5 maps the *status* to behaviour; the *plan* comes from the price the customer is
    paying for. Both are stored, because `can()` needs both (D5): the plan says what was bought
    and the status says whether it is currently paid for. Collapsing them — storing "effective
    plan" alone — would lose the information needed to restore access on reactivation, which
    `PRICING` §4.3 promises is instant and non-destructive.

    **A `canceled` or `unpaid` account keeps its `plan` string.** Entitlements are resolved by
    `limits_for(plan, subscription_status)`, which already maps those statuses down to Free —
    so downgrading the stored plan too would be a second, redundant mechanism, and the one that
    forgets what to restore. This is `PRICING` §4.3's *"nothing was deleted"* applied to the plan
    column itself.

    ## `plan=None` means "do not change the plan", not "Free"

    An `invoice.paid` event carries no line items, and a subscription whose price id is not in
    `PRICE_ENV_VARS` normalizes to `None` (see `prices.plan_for_price_id`, which refuses to guess).
    Treating either as Free would silently downgrade a paying customer on an ordinary receipt.
    The status still updates — that part is always known.
    """
    changed = False

    if state.status is not None and account.subscription_status != state.status:
        account.subscription_status = state.status
        changed = True

    if state.plan is not None and account.plan != state.plan:
        account.plan = state.plan
        changed = True

    if state.provider_subscription_id is not None and (
        account.stripe_subscription_id != state.provider_subscription_id
    ):
        account.stripe_subscription_id = state.provider_subscription_id
        changed = True

    if state.current_period_end is not None and (
        account.current_period_end != state.current_period_end
    ):
        account.current_period_end = state.current_period_end
        changed = True

    if changed:
        session.commit()
        logger.info(
            "subscription state applied: account=%s plan=%s status=%s",
            account.id, account.plan, account.subscription_status,
        )
    return changed


def start_checkout(
    session: Session,
    account: Account,
    *,
    plan: str,
    interval: str,
    success_url: str,
    cancel_url: str,
    provider: object | None = None,
) -> str:
    """Return a hosted checkout URL for `(plan, interval)`. **Never takes a price id** (D3/N2).

    Creates the Stripe Customer on first use and persists `stripe_customer_id`, so a returning
    customer reuses theirs. Two Customers for one account is not cosmetic: the webhook maps
    `provider_customer_id -> account`, so the second Customer's events would resolve to nothing
    and the upgrade would silently never apply.

    **The customer id is committed before checkout is created.** If the order were reversed and
    the commit failed, Stripe would hold a Customer this database has never heard of — and the
    resulting webhook would land in the unmappable bucket. Committing first can at worst leave an
    id for a checkout the user abandoned, which the next attempt reuses.

    `provider` is injectable so tests exercise this against `FakeBillingProvider`; production
    passes nothing and gets the real adapter from the factory.
    """
    from mihomes.services.billing.provider import get_billing_provider

    billing = provider if provider is not None else get_billing_provider("stripe")

    if not account.stripe_customer_id:
        account.stripe_customer_id = billing.create_customer(
            account_id=str(account.id),
            email=_billing_email(session, account),
            name=account.name,
        )
        session.commit()

    return billing.create_checkout_session(
        customer_id=account.stripe_customer_id,
        plan=plan,
        interval=interval,
        success_url=success_url,
        cancel_url=cancel_url,
    )


def start_portal_session(
    account: Account, *, return_url: str, provider: object | None = None
) -> str:
    """Return a Stripe Customer Portal URL — plan changes, payment methods, cancellation.

    Raises `BillingProviderError` when the account has no Stripe Customer, which is the Free
    case (D4): there is nothing to manage, and sending the user to a portal for a customer that
    does not exist would fail inside Stripe's UI rather than here.
    """
    from mihomes.services.billing.provider import BillingProviderError, get_billing_provider

    if not account.stripe_customer_id:
        raise BillingProviderError(
            "this account has no billing customer yet — Free accounts have no Stripe "
            "subscription object (D4), so there is nothing to manage"
        )

    billing = provider if provider is not None else get_billing_provider("stripe")
    return billing.create_portal_session(
        customer_id=account.stripe_customer_id, return_url=return_url,
    )


def _billing_email(session: Session, account: Account) -> str:
    """The owner's email — Stripe needs one for receipts and dunning.

    Resolved from the **owner membership** rather than from whoever is calling: `billing.manage`
    is owner-only today (row 15), but that is an authorization fact and this is a data fact, and
    coupling them would put the wrong address on invoices the first time the matrix changes.
    """
    from mihomes.models.membership import Membership
    from mihomes.models.user import User

    email = session.execute(
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.account_id == account.id,
            Membership.role == "owner",
            # `status` matters here, not just `role`: SPEC-002's partial unique index is scoped
            # to `role = 'owner' AND status = 'active'`, so a revoked former owner can still hold
            # an owner-role row. Omitting this would let a removed person keep receiving the
            # account's invoices and dunning mail.
            Membership.status == "active",
        )
        .limit(1)
    ).scalar_one_or_none()

    if email is None:
        # Every account has an owner (SPEC-002 D4's partial unique index), so this is a
        # corrupted-state signal rather than an ordinary branch — say so instead of sending
        # Stripe an empty string it will happily accept.
        raise ValueError(f"account {account.id} has no owner membership; cannot bill it")
    return email


def _record_event(session: Session, event: NormalizedEvent) -> ProcessedWebhookEvent | None:
    """Insert the ledger row. `None` means this event was already processed.

    **Insert-first, and the `IntegrityError` is the answer** (N4) — not an error path bolted onto
    a check, but the dedup mechanism itself. Under concurrent delivery exactly one insert wins;
    the loser's transaction is rolled back and it returns `None`, which is the correct outcome
    rather than a failure to handle.

    Committed on its own so the record of *having seen* the event survives a later failure in
    processing it.
    """
    row = ProcessedWebhookEvent(
        provider=PROVIDER,
        provider_event_id=event.raw_event_id,
        event_type=event.type,
        occurred_at=event.occurred_at,
        processed_at=datetime.now(UTC),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    return row


def _resolve_account(session: Session, provider_customer_id: str) -> Account | None:
    """`provider_customer_id -> Account` — the mapping D2 keeps out of the adapter.

    **Reads `accounts` with no tenant context bound**, which is only possible because `accounts`
    is the tenant *root* and carries no RLS policy of its own. That is the same property the
    webhook route depends on, and it is why this lookup belongs here rather than behind the
    scoped session.
    """
    return session.execute(
        select(Account).where(Account.stripe_customer_id == provider_customer_id)
    ).scalar_one_or_none()


def _is_stale(session: Session, event: NormalizedEvent, account: Account) -> bool:
    """Has a **newer** state-bearing event already been applied for this account?

    Only state-bearing types are compared (`_STATE_BEARING`) and only against each other. An
    `invoice.paid` cannot make a `subscription.updated` stale: they answer different questions,
    and letting a receipt suppress a plan change would be a subtler version of the bug this
    guards against.

    Ties are **not** stale. Two events with identical timestamps are unordered by definition, and
    dropping one would be an arbitrary choice dressed as a rule; the idempotency ledger already
    guarantees each is applied at most once.
    """
    if event.type not in _STATE_BEARING:
        return False

    newest = session.execute(
        select(ProcessedWebhookEvent.occurred_at)
        .where(
            ProcessedWebhookEvent.account_id == account.id,
            ProcessedWebhookEvent.provider == PROVIDER,
            ProcessedWebhookEvent.event_type.in_(_STATE_BEARING),
            ProcessedWebhookEvent.provider_event_id != event.raw_event_id,
            ProcessedWebhookEvent.error.is_(None),
        )
        .order_by(ProcessedWebhookEvent.occurred_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    return newest is not None and newest > event.occurred_at


def _mark_error(session: Session, row: ProcessedWebhookEvent, reason: str) -> None:
    """Record *why* an event was not applied.

    The row stays — that is what stops the redelivery — but a bare row would be
    indistinguishable from a successfully applied one, which matters twice: a human debugging a
    billing incident needs to know, and `_is_stale` excludes errored rows so a dropped event
    cannot itself become the timestamp that suppresses a later real one.
    """
    row.error = reason
    session.commit()
