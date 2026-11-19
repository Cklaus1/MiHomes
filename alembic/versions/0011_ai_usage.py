"""ai_usage_events + ai_usage_rollups — the AI usage meter (SPEC-004 §4.2, Step 10).

**Both tenant-scoped, with RLS**, which is the opposite call from `0010`'s webhook ledger one
migration earlier — worth stating because the two arrived together and a reader will wonder.

The ledger is global because a Stripe webhook is recorded *before* we know whose it is. A usage
row is only ever created **while an account is bound**: a user made a call, in a request that
already resolved their account. There is no before-we-know-whose-it-is problem here, and the
numbers are billing data — how much of a household's quota was spent, and on what — that one
tenant must never read for another.

`period_start`/`period_end` are `Date`, not timestamps: `PRICING` §5.1 resets on the billing
anniversary, which is a calendar concept. A timestamp would invite timezone arithmetic into a
question that does not have a time of day.

`calls_used` carries a server default of 0 so a row created by the insert-then-increment path is
never briefly NULL — the counter is read on the hot path of every AI call, and NULL there would
propagate into the cap comparison as a silent pass.

Revision ID: 0011_ai_usage
Revises: 0010_processed_webhook_events
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_ai_usage"
down_revision: Union[str, None] = "0010_processed_webhook_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_point", sa.String(50), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("method", sa.String(30), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ai_usage_events_account_id"), "ai_usage_events", ["account_id"], unique=False
    )

    op.create_table(
        "ai_usage_rollups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warned_80_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warned_100_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # The idempotency guarantee for the counter: one row per account per period. Load-bearing
        # rather than descriptive — `record_usage` relies on it to resolve a concurrent insert
        # instead of producing two half-counters.
        sa.UniqueConstraint(
            "account_id", "period_start", name="uq_ai_usage_rollup_account_period"
        ),
    )
    op.create_index(
        op.f("ix_ai_usage_rollups_account_id"), "ai_usage_rollups", ["account_id"], unique=False
    )

    # RLS + drift guards, per the pattern `0007_telegram_links` established: `0002_rls` is scoped
    # to the tables present at its own point in the chain, so the migration that creates a tenant
    # table owns its policy (SPEC-002 A8).
    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for table in ("ai_usage_events", "ai_usage_rollups"):
            for stmt in policy_statements(table):
                op.execute(stmt)
        for stmt in trigger_ddl_statements(
            Base.metadata, only_tables={"ai_usage_events", "ai_usage_rollups"}
        ):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_usage_rollups_account_id"), table_name="ai_usage_rollups")
    op.drop_table("ai_usage_rollups")
    op.drop_index(op.f("ix_ai_usage_events_account_id"), table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
