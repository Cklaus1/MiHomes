"""campaign_enrolments + account_deletion_requests — the last two Phase 4 tables (SPEC-005 §4.4).

Both ordinary tenant tables: RLS policy and drift-guard trigger emitted for each.

## Why §4.4's "five tables, one migration" is four migrations

§4.4 describes one revision creating all five. It cannot be built that way:
`test_pg_baseline.py::test_baseline_matches_metadata` compares `Base.metadata` against the migrated
schema and fails the moment a model exists without a migration — and this phase's models land at
Steps 2, 3, 4 and 6. A single Step 6 migration would leave the suite red across four groups.

So each table shipped with its owning step, the way SPEC-004 shipped `0010` and `0011`:

    0012  email_suppressions          Step 2   global, no RLS (D13/A21)
    0013  email_deliveries            Step 3   tenant
    0014  email_outbox                Step 4   tenant
    0015  campaign_enrolments         Step 6   tenant   <- here
          account_deletion_requests   Step 6   tenant   <- here

**A30 is strictly stronger for the split**: `test_migration_phase4.py` enumerates the Phase 4
revisions from this directory and round-trips each, rather than naming one. Harness deviation D1.

## `account_deletion_requests` takes the mixin's CASCADE, and it never fires

The row is `PRESERVE` in §5.4's disposition table — it is the proof a deletion was honoured, so it
must outlive the data it describes. That reads like an argument for `SET NULL`, and it is not: the
purge enumerates `Base.metadata` **filtered by `TenantOwned`**, and `accounts` is a `GLOBAL_TABLES`
entry outside that sweep. The purge empties an account's tables and never deletes the account row,
so no cascade from `accounts` can reach this one.

Revision ID: 0015_phase4_lifecycle
Revises: 0014_email_outbox
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_phase4_lifecycle"
down_revision: Union[str, None] = "0014_email_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("campaign_enrolments", "account_deletion_requests")


def upgrade() -> None:
    op.create_table(
        "campaign_enrolments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign", sa.String(50), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        # The step INDEX, not the next template name, so O1 can change a sequence's content
        # or its length without a migration (§4.2).
        sa.Column("step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        # Non-NULL means the scheduler skips this row forever — the drip's own idempotency
        # guarantee (A25), whether the sequence finished or the account unenrolled.
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One enrolment per account per campaign. `enrol()` is idempotent because of this,
        # not because it checks first.
        sa.UniqueConstraint(
            "account_id", "campaign", name="uq_enrolment_account_campaign"
        ),
    )
    op.create_index(
        op.f("ix_campaign_enrolments_account_id"),
        "campaign_enrolments",
        ["account_id"],
        unique=False,
    )

    op.create_table(
        "account_deletion_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        # No FK to `users`: that row is global and may itself be deleted, and this record
        # has to survive that too.
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        # Per-table disposition AND row count as JSON (D18) — never a bare total, which
        # cannot distinguish a table that held nothing from one that was missed.
        sa.Column("purge_manifest", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_deletion_requests_account_id"),
        "account_deletion_requests",
        ["account_id"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for table in TABLES:
            for stmt in policy_statements(table):
                op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables=set(TABLES)):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index(
        op.f("ix_account_deletion_requests_account_id"),
        table_name="account_deletion_requests",
    )
    op.drop_table("account_deletion_requests")
    op.drop_index(
        op.f("ix_campaign_enrolments_account_id"), table_name="campaign_enrolments"
    )
    op.drop_table("campaign_enrolments")
