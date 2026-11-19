"""telegram_chat_bindings — a Telegram group bound to one account by /linkchat (2026-10-23).

An ordinary tenant table (RLS policy + drift-guard trigger, emitted the way `0007` and `0016` do),
plus its gateway bootstrap policy: the webhook looks a chat up by `chat_id` before any account is
bound, because that lookup is how the account is found (see `models/telegram_chat_binding.py`).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025_telegram_chat_bindings"
down_revision: Union[str, None] = "0024_gateway_bootstrap_policies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_chat_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("bound_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "bound_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["bound_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", name="uq_telegram_chat_binding_chat"),
    )
    op.create_index(
        op.f("ix_telegram_chat_bindings_account_id"), "telegram_chat_bindings", ["account_id"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        # Tenant policy + the `telegram_chat_bindings_gateway_chat` bootstrap policy.
        for stmt in policy_statements("telegram_chat_bindings"):
            op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables={"telegram_chat_bindings"}):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index(op.f("ix_telegram_chat_bindings_account_id"), table_name="telegram_chat_bindings")
    op.drop_table("telegram_chat_bindings")
