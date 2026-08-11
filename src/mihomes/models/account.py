"""Account — the tenant boundary (SPEC-002 §4.2).

This is the schema of record: `ONBOARDING` §2 and `PRICING` §4.2 defer to it (A3).

**No `owner_user_id`.** Ownership is the partial unique index on `memberships`
(D4/A2). Two sources of truth for who owns an account is how they drift apart.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="household")  # A3

    plan: Mapped[str] = mapped_column(String(20), nullable=False, server_default="free")

    # Written ONLY by the billing webhook handler (BILLING 5-6). DEFERRED (Phase 3).
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subscription_status: Mapped[str | None] = mapped_column(String(30), nullable=True)  # A3: NOT billing_status
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # App-managed no-card trial (PRICING 4.2, BILLING:485). DEFERRED (Phase 3) but the
    # columns ship now so Phase 3 needs no migration on a live table.
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    # NOTE: no owner_user_id. Ownership is the partial unique index on memberships (D4/A2).

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Account {self.slug} ({self.plan})>"
