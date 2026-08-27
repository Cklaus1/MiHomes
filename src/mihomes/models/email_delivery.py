"""`EmailDelivery` — the per-message delivery record (SPEC-005 §4.1, A19).

`SAAS_PRD:168`'s "email delivery tracking", and D7's third observability surface. One row per
message that actually reached a provider, carrying the provider's message id so a support question
— *"we never got the receipt"* — can be answered with the vendor's own identifier rather than a
shrug.

## Separate from `EmailOutbox` on purpose

The outbox is a work queue that drains; this is a permanent record. Merging them means one of two
bad outcomes: either the queue never empties (rows kept forever so the history survives) or the
history is deleted (rows removed once sent). Two tables, two lifetimes.

The consequence for A19 is worth stating: **the row is written next to the successful
`provider.send()` call**, not when a message is enqueued. Step 4 moves that call out of
`EmailService._send` and into the outbox's `drain`, and the write travels with it — so "exactly
one row per send" keeps meaning *per send* rather than per attempt, across a five-rung backoff
ladder where four attempts may fail before one succeeds.

## `status` is nullable, and NULL is the normal terminal state

Provider webhooks update it to `delivered` / `bounced` / `complained` / `opened` when they arrive.
Most messages get no further signal, so NULL means "accepted by the provider, nothing more heard"
— which is success, not an error. A `NOT NULL` default of `"sent"` would make the common case
indistinguishable from a webhook that genuinely reported `sent`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned

__all__ = ["DELIVERY_STATUSES", "EmailDelivery"]

#: Terminal signals a provider webhook may report. NULL — no signal — is not in this set and is
#: the normal case; see the module docstring.
DELIVERY_STATUSES = ("delivered", "bounced", "complained", "opened")


class EmailDelivery(Base, TenantOwned):
    """One row per message handed to a provider and accepted."""

    __tablename__ = "email_deliveries"
    __table_args__ = (
        # Leads with `account_id`, which `test_composite_indexes_lead_with_account_id`
        # requires: under RLS every query is already filtered by account, so an index that
        # leads with `sent_at` cannot serve it without a scan.
        Index("ix_email_delivery_account_sent", "account_id", "sent_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    template: Mapped[str] = mapped_column(String(50), nullable=False)

    # Timezone-aware, matching `0010`/`0012` rather than `TimestampMixin`'s naive columns:
    # compared against injected `now` values in the drain and ladder tests, and a naive-vs-aware
    # comparison raises at runtime rather than at import.
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    provider: Mapped[str] = mapped_column(String(20), nullable=False)

    # Nullable because a provider may accept a message without returning an id — the record of
    # the send is still worth keeping. `ResendProvider` treats a missing id as a send failure,
    # so in practice this is populated; a future provider need not be.
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # One of DELIVERY_STATUSES, or NULL. See the module docstring on why NULL is success.
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # `account_id` comes from `TenantOwned` — declared there with the CASCADE FK and the index,
    # so redeclaring it here would be a second source of truth for the tenancy column the drift
    # guard protects. That is the opposite of the webhook ledger's deliberate FK *absence*: the
    # ledger must outlive the account it describes so a replayed webhook is not reprocessed,
    # whereas a delivery record is that account's own data and is purged with it (A28's DELETE).

    def __repr__(self) -> str:
        return (
            f"<EmailDelivery template={self.template!r} provider={self.provider!r} "
            f"id={self.provider_message_id!r} status={self.status!r}>"
        )
