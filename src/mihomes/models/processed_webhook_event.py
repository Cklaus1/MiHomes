"""`ProcessedWebhookEvent` — the webhook idempotency ledger. **Deliberately not `TenantOwned`** (B7).

A raw webhook arrives *before* we know which account it belongs to: `NormalizedEvent` carries
Stripe's customer id and nothing else, and mapping that to an account is `BillingService`'s job
(D2). Attaching an `account_id` RLS policy here would make every lookup return zero rows under the
webhook route's session — which has no account bound, because there is no session cookie and no
principal — and **every Stripe event would then be reprocessed silently**. Not loudly: the insert
would succeed, the dedup check would find nothing, and a customer would be charged twice or
downgraded twice with no error anywhere.

Same carve-out shape as `sessions`, and for the same underlying reason the registry already
documents: *"read or written BEFORE account context exists, so a tenant policy on any of these
returns zero rows and breaks the thing it is protecting."* `A6` is the test that catches a later
migration adding a policy here.

## The unique constraint is the mechanism, not a hint

`uq_processed_webhook_provider_event` is **not** a bare index. Step 5 inserts first and treats the
unique violation itself as the dedup signal (N4), because `SELECT`-then-`INSERT` races: two
concurrent deliveries of the same event both see "not present" and both process. Stripe retries on
any non-2xx and delivers concurrently under load, so this is an ordinary case rather than a
pathological one. Dropping the constraint would leave every test green and the guarantee gone.

## `account_id` is nullable, and that is a legitimate state

An event for a customer we cannot resolve — a Stripe account shared with another environment, a
customer deleted on our side, a test-mode event reaching a live endpoint — is still **recorded**,
so it is not retried forever. `error` carries why. A nullable column here means "we saw this and
could not place it", which is different from "we have not seen this", and only the ledger can tell
those apart.

## PK type: `PGUUID`, not `String(36)`

SPEC-004 §4.1 writes `String(36)` with `default=new_id`, which was correct when the spec was
written (2026-08-04) and is not now — SPEC-002 moved the tree to native UUID columns. Transcribing
§4 verbatim would fail `test_baseline_matches_metadata`, the same gate that caught a naive-vs-aware
`DateTime` in migration 0009. Recorded as **C6** in the build harness.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base

__all__ = ["ProcessedWebhookEvent"]


class ProcessedWebhookEvent(Base):
    """Webhook idempotency ledger. Global, no RLS — see the module docstring."""

    __tablename__ = "processed_webhook_events"
    __table_args__ = (
        # THE idempotency guarantee (N4). Insert-first relies on the violation as the signal.
        UniqueConstraint(
            "provider", "provider_event_id", name="uq_processed_webhook_provider_event"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # "stripe"
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # For out-of-order handling (`BILLING` §6): the **provider's** timestamp, not ours. Ours would
    # order by when we happened to receive the retry, which is precisely the ordering that is
    # wrong when Stripe redelivers an older event after a newer one.
    #
    # Timezone-aware on both, unlike the `TimestampMixin` columns elsewhere: these are compared
    # against `NormalizedEvent.occurred_at`, which comes from a Unix timestamp in UTC, and a naive
    # comparison against an aware datetime raises at runtime rather than at import.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Set when the event resolved to an account. NULL is legitimate — see the docstring.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ProcessedWebhookEvent {self.provider}:{self.provider_event_id} "
            f"({self.event_type})>"
        )
