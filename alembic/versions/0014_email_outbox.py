"""email_outbox — the queue `BILLING` §2.4 names and never specifies (SPEC-005 §4.1, D12).

An ordinary tenant table: RLS policy and drift-guard trigger both emitted, like `0013`.

## The index leads with `account_id`, unlike §4.1's declaration

§4.1 writes `Index("ix_email_outbox_due", "next_attempt_at", "sent_at")` — an index for a global
"every due row across every account, oldest first" scan. **That query returns zero rows.**
Measured through the app role with the GUC cleared: the RLS predicate is `account_id = NULL`,
which is NULL rather than true, so nothing matches for any account.

So `drain` binds context per account and the CLI job sweeps accounts, exactly as SPEC-004's
`reconcile` and `trial-sweep` already do. The index leads with `account_id` accordingly, which is
also what `test_composite_indexes_lead_with_account_id` requires — claiming an
`EXPECTED_NON_LEADING` exemption here would have been buying an exception to serve a query that
can never return a row.

Recorded as harness deviation D3.

Revision ID: 0014_email_outbox
Revises: 0013_email_deliveries
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_email_outbox"
down_revision: Union[str, None] = "0013_email_deliveries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_address", sa.String(320), nullable=False),
        sa.Column("template", sa.String(50), nullable=False),
        # The render CONTEXT as JSON, never the rendered html: a template fix must repair
        # mail that is already queued (§4.1).
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("klass", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        # A dead row is KEPT, never deleted — "why did the customer not get their receipt"
        # is a support question, and a deleted row answers it with silence.
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_email_outbox_account_id"), "email_outbox", ["account_id"], unique=False
    )
    op.create_index(
        "ix_email_outbox_due",
        "email_outbox",
        ["account_id", "next_attempt_at", "sent_at"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for stmt in policy_statements("email_outbox"):
            op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables={"email_outbox"}):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index("ix_email_outbox_due", table_name="email_outbox")
    op.drop_index(op.f("ix_email_outbox_account_id"), table_name="email_outbox")
    op.drop_table("email_outbox")
