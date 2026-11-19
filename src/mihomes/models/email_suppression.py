"""`EmailSuppression` — addresses that must never receive lifecycle mail (SPEC-005 D13).

**Deliberately not `TenantOwned`** — the third carve-out in the tree, after `sessions` (SPEC-002
§7) and `processed_webhook_events` (SPEC-004 §4.1), and the reason is different from both.

Those two are global because they are *read before account context exists*. This one is global
because **suppression is a property of an address, not of an account.** Someone who unsubscribes,
hard-bounces, or files a spam complaint must stay suppressed even if they later appear under a
second account — as an invited staff member, as a second signup, as a vendor contact. Scoping the
list per-tenant would re-mail a complainer the first time they were invited elsewhere, which is
how a sending domain gets blocklisted.

It follows that the row **survives account deletion untouched** (A29). That reads backwards at
first — an erasure request that leaves an email address behind — and is deliberate: forgetting
that someone asked never to be contacted is not privacy, it is the failure the request was
protecting against. The row holds an address and a reason, nothing else about the person.

## No RLS policy, and A21 is the gate on that

The migration (§4.4) enables RLS on this phase's four tenant tables and **not** on this one. A
later migration that "helpfully" adds a policy here would silently scope the list per-account:
every `is_suppressed` check under a bound account would return `False` for an address suppressed
elsewhere, and the mail would go out. Nothing would raise. `A21` exists to fail that migration.

## PK type: `PGUUID`, not `String(36)`

§4.1 writes `String(36)` with `default=new_id`, correct when the spec was written (2026-08-04) and
not now — SPEC-002 moved the tree to native UUID columns. Transcribing verbatim would fail
`test_baseline_matches_metadata`. Same correction as SPEC-004's C6.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base

__all__ = ["EmailSuppression"]

#: Why an address is on the list. `unsubscribe` is user-initiated; the other three are not.
SUPPRESSION_REASONS = ("unsubscribe", "hard_bounce", "complaint", "manual")


class EmailSuppression(Base):
    """A suppressed address. Global, no RLS — see the module docstring."""

    __tablename__ = "email_suppressions"
    __table_args__ = (
        # THE idempotency guarantee. `suppress()` inserts first and treats the violation
        # as the signal (the same discipline as the webhook ledger): bounces and
        # complaints arrive more than once, and concurrently, so check-then-insert races.
        UniqueConstraint("address", name="uq_email_suppression_address"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    address: Mapped[str] = mapped_column(String(320), nullable=False)

    # One of SUPPRESSION_REASONS. Kept as free text rather than an enum for the same
    # reason the webhook ledger does: a new provider signal must never fail an insert
    # whose whole purpose is recording that we saw something.
    reason: Mapped[str] = mapped_column(String(20), nullable=False)

    # Timezone-aware, like the webhook ledger's columns and unlike `TimestampMixin`:
    # compared against injected `now` values in the drain and ladder tests, and a naive
    # comparison against an aware datetime raises at runtime rather than at import.
    suppressed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # The provider event that caused it, when there was one. NULL for a user-clicked
    # unsubscribe — which is the common case, not an exception.
    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<EmailSuppression address={self.address!r} reason={self.reason!r} "
            f"at={self.suppressed_at!r}>"
        )
