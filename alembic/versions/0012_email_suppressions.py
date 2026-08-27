"""email_suppressions — the suppression list (SPEC-005 D13, §4.1).

**The second migration in this tree that deliberately creates a table with no RLS policy and no
drift-guard trigger.** `0010` was the first. The omission is the design, not an oversight, and the
reason here is different from `0010`'s.

`0010`'s ledger is global because it is written *before* account context exists. This table is
global because **suppression is a property of an address, not of an account.** Someone who
unsubscribes, hard-bounces, or files a spam complaint must stay suppressed even if they later
appear under a second account — invited as staff, signing up again, listed as a vendor contact.
A tenant policy here would make `is_suppressed` return `False` for an address suppressed
elsewhere, under any bound account, and the mail would go out. Nothing would raise. That is how a
sending domain gets blocklisted.

`A21` (`test_suppression_not_rls`) is the test that catches a later migration adding a policy.

## Why this ships separately from §4.4's other four tables

§4.4 describes **one** Phase 4 migration creating five tables. It cannot be built that way:
`test_baseline_matches_metadata` compares `Base.metadata` against the migrated schema and fails
the moment a model exists without a migration. Since the suppression model lands at Step 2 and the
outbox at Step 4, a single Step 6 migration would leave the suite red across four groups.

So §4.4 is split by the table's owning step, which is also how SPEC-004 shipped `0010` and `0011`.
A30 covers every Phase 4 migration's round-trip rather than one — a strictly stronger check, since
each is exercised independently.

## The unique constraint is the mechanism

`uq_email_suppression_address` is not a bare index. `suppress()` inserts first and treats the
violation as the dedup signal, because `SELECT`-then-`INSERT` races: bounce and complaint webhooks
for one address arrive more than once and concurrently. Drop the constraint and every test stays
green while A22's guarantee is gone.

## No `account_id` at all

Not "nullable and not tenancy", as in `0010` — the column does not exist. There is nothing here to
scope on, which is what makes this the clean `GLOBAL_TABLES` entry the registry describes. It also
means the row survives account deletion untouched (A29): forgetting that someone asked never to be
contacted is not privacy, it is the failure the request was protecting against.

Revision ID: 0012_email_suppressions
Revises: 0011_ai_usage
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_email_suppressions"
down_revision: Union[str, None] = "0011_ai_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_suppressions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        # Timezone-aware, matching the model and `0010`'s columns rather than the
        # `TimestampMixin` naive default: compared against injected `now` values, and a
        # naive-vs-aware comparison raises at runtime rather than at import.
        sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("address", name="uq_email_suppression_address"),
    )

    # **No `policy_statements` and no `trigger_ddl_statements` call here.** Every migration
    # that creates a tenant table emits both; this one must not, and A21 asserts the absence.
    # If you are reading this because a test failed after you added them, the failure is the
    # design working.


def downgrade() -> None:
    op.drop_table("email_suppressions")
