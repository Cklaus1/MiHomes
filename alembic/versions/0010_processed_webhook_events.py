"""processed_webhook_events — the webhook idempotency ledger (SPEC-004 B7).

**The one migration in this tree that deliberately creates a table with no RLS policy and no
drift-guard trigger**, and the omission is the point rather than an oversight.

A Stripe webhook is verified and recorded *before* we know which account it belongs to: the event
carries a provider customer id and nothing else, and resolving it to an account is
`BillingService`'s job (D2). Under an `app.current_account` policy every dedup lookup would run on
the webhook route's account-less session and return zero rows — so the insert would succeed, the
check would find nothing, and **every Stripe event would silently reprocess**. Not loudly: a
customer charged twice, or downgraded twice, with no error anywhere. Same carve-out as `sessions`,
for the same reason the registry states — *"read or written BEFORE account context exists."*

`A6` (`test_ledger_not_rls`) is the test that catches a later migration adding a policy here.

## The unique constraint is the mechanism

`uq_processed_webhook_provider_event` is not a bare index. Step 5 inserts first and treats the
unique violation itself as the dedup signal (N4), because `SELECT`-then-`INSERT` races: two
concurrent deliveries both see "not present" and both process. Stripe retries on any non-2xx and
delivers concurrently under load, so that is an ordinary case. Drop the constraint and every test
stays green while the guarantee is gone.

## `account_id` is here but is not tenancy

It records which account an event *resolved to* — an output of processing, never an input to
visibility — and is legitimately NULL when an event resolved to none. It carries **no foreign key**
to `accounts`: a `CASCADE` would delete the processing history when an account is deleted, and
`RESTRICT` would block the deletion outright. The ledger must outlive what it describes, or a
replayed webhook for a deleted account would be processed as if new.

Indexed because Step 18's reconciliation sweep and any billing-incident investigation both start
from "what happened for this account".

Revision ID: 0010_processed_webhook_events
Revises: 0009_document_access
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_processed_webhook_events"
down_revision: Union[str, None] = "0009_document_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        # Timezone-aware, unlike the `TimestampMixin` columns elsewhere: both are compared
        # against `NormalizedEvent.occurred_at`, which is built from a Unix timestamp in UTC, and
        # comparing naive to aware raises at runtime rather than at import. Migration 0009's note
        # is the mirror image of this one — there the mixin's naive columns were the right match
        # and copying an aware column was the mistake.
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        # No ForeignKeyConstraint — see the module docstring.
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "provider_event_id", name="uq_processed_webhook_provider_event"
        ),
    )
    op.create_index(
        op.f("ix_processed_webhook_events_account_id"),
        "processed_webhook_events",
        ["account_id"],
        unique=False,
    )

    # **No `policy_statements` and no `trigger_ddl_statements` call here.** Every other migration
    # that creates a table emits both; this one must not, and A6 asserts the absence. If you are
    # reading this because a test failed after you added them, the failure is the design working.


def downgrade() -> None:
    op.drop_index(
        op.f("ix_processed_webhook_events_account_id"),
        table_name="processed_webhook_events",
    )
    op.drop_table("processed_webhook_events")
