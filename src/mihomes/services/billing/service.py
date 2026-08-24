"""`BillingService` — everything the adapter must not do: the DB, account mapping, idempotency.

The seam (D2/N5): **adapter = vendor I/O + normalization; service = state + business rules.**
`NormalizedEvent` arrives carrying Stripe's customer id and no `account_id`, and resolving that is
this module's job.

**Deliberately partial at Step 4.** `handle_verified_event` exists so the webhook route has
something to hand a verified event to; the idempotency ledger write, the account mapping, the
out-of-order drop and the `BILLING` §5 status mapping are Steps 5 and 7. The alternative —
inlining a first draft of that sequence in the route — is the shape D2 exists to prevent: the
reconciliation sweep (Step 18) applies the *same* state through a different entry point, and two
implementations of "apply this subscription state" drift, which is a class of bug that shows up as
a customer's plan disagreeing with their invoice.

What is already true and load-bearing: **the route never writes to the database.** It verifies and
delegates. That boundary is what Step 5 fills in behind, rather than refactors.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from mihomes.services.billing.provider import NormalizedEvent

logger = logging.getLogger(__name__)

__all__ = ["handle_verified_event"]


def handle_verified_event(session: Session, event: NormalizedEvent) -> None:
    """Apply a **signature-verified** event. Idempotency and mapping land at Step 5.

    The name says `verified` because that precondition is the route's to establish and this
    function's to assume — a future caller that hands over an unverified event is the defect N3
    describes, and naming the parameter is the cheapest guard against it.

    Logs rather than raises for now: an exception here would propagate to the route's 500 branch
    and make Stripe retry an event the system does not yet handle, which would look like a
    transient outage rather than an unimplemented step.
    """
    logger.info(
        "stripe webhook received: type=%s event_id=%s customer=%s",
        event.type,
        event.raw_event_id,
        event.provider_customer_id,
    )
