"""email_deliveries — the per-message delivery log (SPEC-005 §4.1, A19).

Unlike `0012`, this is an ordinary **tenant** table: RLS policy and drift-guard trigger both
emitted, per the pattern `0007_telegram_links` established — `0002_rls` is scoped to the tables
present at its own point in the chain, so the migration that creates a tenant table owns its
policy (SPEC-002 A8).

The two neighbours make the contrast worth stating once, because all three shipped within a few
revisions of each other and only one of the three shapes is the default:

* `0010` — global, **no FK to accounts at all**: the webhook ledger must outlive the account it
  describes, or a replayed event for a deleted account is processed as if new.
* `0012` — global, **no account column at all**: suppression belongs to an address.
* `0013` — tenant, `CASCADE` FK from `TenantOwned`: a delivery record is the account's own data
  and is purged with it (A28's `DELETE` disposition).

## The composite index leads with `account_id`

`test_composite_indexes_lead_with_account_id` requires it, and the reason is not stylistic: under
RLS every query already carries an `account_id` predicate, so an index leading with `sent_at`
cannot serve it without a scan.

Revision ID: 0013_email_deliveries
Revises: 0012_email_suppressions
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_email_deliveries"
down_revision: Union[str, None] = "0012_email_suppressions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_address", sa.String(320), nullable=False),
        sa.Column("template", sa.String(50), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        # NULL is the normal terminal state — "accepted, no further signal" — not an error.
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("status_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_email_deliveries_account_id"),
        "email_deliveries",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_delivery_account_sent",
        "email_deliveries",
        ["account_id", "sent_at"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for stmt in policy_statements("email_deliveries"):
            op.execute(stmt)
        for stmt in trigger_ddl_statements(
            Base.metadata, only_tables={"email_deliveries"}
        ):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index("ix_email_delivery_account_sent", table_name="email_deliveries")
    op.drop_index(op.f("ix_email_deliveries_account_id"), table_name="email_deliveries")
    op.drop_table("email_deliveries")
