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
from mihomes.services.billing.provider import NormalizedEvent

logger = logging.getLogger(__name__)

__all__ = ["handle_verified_event"]

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

    # Step 7 lands `apply_subscription_state` here — the single writer of plan /
    # subscription_status / current_period_end, shared with the reconciliation sweep so live
    # events and drift-correction cannot diverge.
    logger.info(
        "stripe webhook applied: type=%s event=%s account=%s",
        event.type, event.raw_event_id, account.id,
    )


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
