"""`CampaignEnrolment` — how far through a drip an account is (SPEC-005 §4.2).

One row per `(account, campaign)`. **The step index is stored, not the next template name**, so
O1 can change a sequence's content — or its length — without a migration.

## A shortened sequence completes, it does not error

O1 is an open decision: the founder sets the drip content and cadence, and may well shorten a
sequence after enrolments already exist. A row whose `step` exceeds the new length is
**completed**, not a fault (§5.3). Treating it as an error would turn an ordinary content edit
into a nightly job that fails for exactly the accounts furthest along.

## `completed_at` is the idempotency guarantee

Non-NULL means the scheduler skips this row forever — whether the sequence finished or the
account unenrolled. The drip job's "send each step once and never twice" (A25) rests on this
column plus the unique constraint, not on the job remembering what it did.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned

__all__ = ["CampaignEnrolment"]


class CampaignEnrolment(Base, TenantOwned):
    """One account's position in one drip campaign."""

    __tablename__ = "campaign_enrolments"
    __table_args__ = (
        # THE enrolment guarantee: one row per account per campaign. `enrol()` is idempotent
        # because of this constraint, not because it checks first — the same insert-first
        # discipline as the suppression list and the webhook ledger.
        UniqueConstraint("account_id", "campaign", name="uq_enrolment_account_campaign"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    # "onboarding" | "reengagement" — free text rather than an enum, because O1 may add a
    # sequence and a new campaign name must not require a migration.
    campaign: Mapped[str] = mapped_column(String(50), nullable=False)

    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set when the sequence finishes OR the account unenrols. See the module docstring.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<CampaignEnrolment campaign={self.campaign!r} step={self.step} "
            f"completed={self.completed_at is not None}>"
        )
