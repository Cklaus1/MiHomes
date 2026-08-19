"""Onboarding progress — SPEC-003 §4.2, `ONBOARDING` §5 (A17, A18).

*"Onboarding is idempotent/resumable: if the user drops off after step 2, next sign-in resumes at
step 3 (account exists, no home yet)."*

**Why a table rather than inferring progress from the data.** Resumption could almost be derived —
an account with no properties is "at step 3" — but only for the *mandatory* steps. Steps 4 and 5
are skippable, and skipping is a first-class path (`ONBOARDING` §5), so "no spaces yet" is
ambiguous between *not yet asked* and *asked and declined*. Inferring would re-prompt a user who
already said no, on every sign-in. `completed_steps` records the difference.

`finished_at` is separate from "all steps completed" for the same reason: a user who skips 4 and 5
finishes onboarding without completing them.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.models import Base, TenantOwned


class OnboardingState(TenantOwned, Base):
    """One row per account, keyed **on** the account — §4.2 verbatim.

    `account_id` is declared here rather than inherited so it can be the primary key, which is
    what makes "one row per account" a schema fact rather than a convention. The declaration
    overrides `TenantOwned`'s `@declared_attr`; the column keeps the same FK and cascade.

    **It is `TenantOwned`, and that has a consequence the service must handle.** The G8 filter
    therefore applies to every read — including the ones onboarding performs *before* the session
    has selected an account, which is most of them. `onboarding_service` binds
    `account_context(account_id)` explicitly around its queries for that reason: it always knows
    which account it is asking about, so it does not need the ambient context to be right.

    The alternative — classifying it GLOBAL — was rejected: the tenancy registry forbids a global
    table from carrying `account_id` (SPEC-002 D3), and that invariant is worth more than the
    convenience. A table with an `account_id` that nothing scopes is exactly the shape of a leak.
    """

    __tablename__ = "onboarding_state"

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    completed_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OnboardingState steps={self.completed_steps} finished={self.finished_at}>"
