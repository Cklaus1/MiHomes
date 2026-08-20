"""telegram_links — SPEC-003 §4.2, D19 (A28, A32).

Keyed on `memberships`, never on `Staff` (N6): `memberships.role` is the capability matrix's
vocabulary, while `StaffRole` is a *job* enum containing its own `OWNER`. Crossing them would make
a housekeeping "owner" an account owner.

`ondelete="CASCADE"` is what makes `TELEGRAM_PRD:158`'s *"revoking a membership implicitly revokes
the link"* true **by construction** rather than by a code path remembering to do it (A32).

The lookup index is deliberately **not** led by `account_id`, unlike every other tenant index in
this schema: the bot resolves a sender before it knows which account they belong to — that
resolution is how the account is discovered — so leading with `account_id` would leave the only
query this table exists to serve unindexed.

Revision ID: 0007_telegram_links
Revises: 0006_user_last_used_account
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_telegram_links"
down_revision: Union[str, None] = "0006_user_last_used_account"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["membership_id"], ["memberships.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "telegram_user_id", name="uq_telegram_link_account_user"
        ),
    )
    op.create_index(
        op.f("ix_telegram_links_account_id"), "telegram_links", ["account_id"], unique=False
    )
    op.create_index(
        "ix_telegram_links_lookup", "telegram_links", ["telegram_user_id"], unique=False
    )

    # RLS **and** the drift guard for the table this migration creates. `0002_rls` and
    # `0001_pg_baseline` both ran before `telegram_links` existed, and each is scoped to the
    # tables present at its own point in the chain — so the migration that creates a tenant table
    # owns both its policy (SPEC-002 A8) and its guard trigger.
    #
    # `membership_id` is a guarded link: the trigger refuses a row whose `account_id` disagrees
    # with its membership's, which is the denormalisation cost `TenantOwned` documents.
    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for stmt in policy_statements("telegram_links"):
            op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables={"telegram_links"}):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index("ix_telegram_links_lookup", table_name="telegram_links")
    op.drop_index(op.f("ix_telegram_links_account_id"), table_name="telegram_links")
    op.drop_table("telegram_links")
