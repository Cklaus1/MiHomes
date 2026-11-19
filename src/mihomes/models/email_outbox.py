"""`EmailOutbox` — queued mail awaiting delivery (SPEC-005 §4.1, D12).

**The thing `BILLING` §2.4 names once and never specifies.** A table, not an in-process retry
loop: a retry loop dies with the request and cannot survive a deploy, which makes it useless for
precisely the billing-critical mail §2.4 is talking about. A row survives both.

`TenantOwned` because a queued message belongs to the account it is about — an operator listing
one account's pending mail must not see another's.

## `context` holds the render context, not rendered html

Rendered at **send** time, so a template fix repairs mail that is already queued. That is also
why the column is JSON rather than two text columns holding a subject and a body.

This resolves a contradiction in the spec, recorded as harness deviation D4: §5.2 gives `_send`'s
order as *"suppression check → render → unsubscribe headers → enqueue"*, while §4.1 says
*"Rendered at SEND time, not enqueue time."* Both cannot hold. The model comment wins because it
is load-bearing — it is the reason this column exists at all — so suppression is checked at
enqueue and rendering happens in `drain`.

## The index leads with `account_id`, unlike §4.1's

§4.1 declares `Index("ix_email_outbox_due", "next_attempt_at", "sent_at")`, which is designed for
a global "every due row across all accounts, oldest first" scan. **Measured: that query returns
zero rows.** With no account bound the RLS predicate evaluates `account_id = NULL`, which is NULL
rather than true, for every row — so the index would serve a query that can never return anything,
and `test_composite_indexes_lead_with_account_id` would reject it besides.

So `drain` binds context per account and sweeps, exactly as SPEC-004's `mihomes jobs reconcile`
and `trial-sweep` already do (`cli/jobs.py`: *"Both jobs sweep across accounts, which is why they
bind context per account"*). Harness deviation D3.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned

__all__ = ["BACKOFF_LADDER", "MAX_ATTEMPTS", "EmailOutbox"]

#: The gaps between attempts, in order. `BACKOFF_LADDER[n]` is the wait after the (n+1)-th
#: failure.
#:
#: **The spec's arithmetic does not reconcile and this is the resolution** (A16). §5.3 says
#: *"1m, 5m, 30m, 2h, 12h — five attempts, then `failed_at` is set"*, but five attempts need
#: only **four** gaps: attempt 1 fails → wait → 2 → wait → 3 → wait → 4 → wait → 5, and the
#: fifth failure is terminal rather than scheduled. Listing five intervals for five attempts
#: counts the gaps as if the last failure were followed by a sixth try.
#:
#: `12h` is therefore unreachable by construction. Kept in the tuple and asserted as unreachable
#: rather than deleted, so a later reader who wants six attempts finds the value already chosen —
#: and so the discrepancy is visible instead of quietly resolved in either direction.
BACKOFF_LADDER = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
)

#: Attempts before a row is marked failed and stops being selected (A16).
MAX_ATTEMPTS = 5


class EmailOutbox(Base, TenantOwned):
    """One queued message."""

    __tablename__ = "email_outbox"
    __table_args__ = (
        # The drain worker's only query: this account's due, unsent rows, oldest first.
        # Leads with `account_id` — see the module docstring on why the spec's ordering
        # describes a query that cannot return a row.
        Index("ix_email_outbox_due", "account_id", "next_attempt_at", "sent_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    template: Mapped[str] = mapped_column(String(50), nullable=False)

    # JSON. The render CONTEXT, never the rendered html — see the module docstring.
    context: Mapped[str] = mapped_column(Text, nullable=False)

    # "transactional" | "lifecycle" — decides whether suppression applies (D13). Carried on
    # the row rather than re-derived at drain time from the template name: a template's class
    # is a decision made at the call site, and re-deriving it would let a rename change
    # whether queued mail is suppressible.
    klass: Mapped[str] = mapped_column(String(20), nullable=False)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set when attempts are exhausted. A dead row is KEPT, never deleted: "why did the
    # customer not get their receipt" is a question someone will ask in support, and a
    # deleted row answers it with silence.
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<EmailOutbox template={self.template!r} attempts={self.attempts} "
            f"sent={self.sent_at is not None} failed={self.failed_at is not None}>"
        )
