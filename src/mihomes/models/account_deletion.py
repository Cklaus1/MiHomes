"""`AccountDeletionRequest` — the two-phase deletion state machine (SPEC-005 §4.3, D15).

`requested` → (grace, O2) → `purged`.

## This row outlives the data it describes — but not the account row

After a purge every `TenantOwned` row has been deleted or anonymized (D18), and **this record
remains** as proof the request was honoured and when. That is the artifact a regulator asks for,
so §5.4 lists it under `PRESERVE` explicitly and A29 asserts it survives untouched.

"Outlives" needed checking rather than assuming, because it decides this table's FK. §5.4's purge
enumerates from `Base.metadata` **filtered by `TenantOwned`**, and `accounts` is a `GLOBAL_TABLES`
entry — the tenant root, invisible to that sweep. So the purge empties an account's tables and
never deletes the account row itself.

That makes the ordinary `TenantOwned` shape correct here: `account_id` NOT NULL with the mixin's
`CASCADE` FK, and **the cascade never fires**, because nothing deletes the parent. A first draft
of this file reached for `SET NULL` and a nullable column to protect the record from a cascade
that cannot happen — and `test_each_tenant_table_has_account_id` rejected it, correctly. The gate
was right; the design was solving an imagined problem.

## What "purged" looks like afterwards

The account row survives with no data under it and no memberships, so nothing grants app access to
these rows — they are readable by an operator with database access, which is the right end state
for a compliance artifact: retained, and not served to anyone through the product.

## `purge_manifest` is evidence, not decoration

Per-table **disposition and row count** at purge time, as JSON (D18): `deleted` / `preserved` /
`anonymized`, never a bare total. It is how A28 proves every table was both *reached* and
*dispositioned*, and how support answers "what was deleted" versus "what was kept without your
name on it". A count alone cannot distinguish a table that held nothing from one that was missed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned

__all__ = ["AccountDeletionRequest"]


class AccountDeletionRequest(Base, TenantOwned):
    """One deletion request, and its outcome."""

    __tablename__ = "account_deletion_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Who asked. **Not** an FK to `users`: the user row is global and may itself be deleted,
    # and this record has to survive that too. Recorded as an id for an operator to trace.
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )

    # When the grace period ends. O2 sets its length; the state machine is identical either way.
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # JSON: per-table disposition and row count. See the module docstring.
    purge_manifest: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        state = (
            "purged" if self.purged_at
            else "cancelled" if self.cancelled_at
            else "requested"
        )
        return f"<AccountDeletionRequest {state} purge_after={self.purge_after!r}>"
