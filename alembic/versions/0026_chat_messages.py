"""chat_messages — a bound group's stored conversation for the Conversations page (2026-10-27).

An ordinary tenant table: RLS policy and drift-guard trigger emitted via `policy_statements` /
`trigger_ddl_statements`, the way `0007`, `0016` and `0025` do it. Read only by owners/admins at
`/conversations`; written from `review_common.handle_inbound` inside the bound account.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026_chat_messages"
down_revision: Union[str, None] = "0025_telegram_chat_bindings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("chat_id", sa.String(length=100), nullable=False),
        sa.Column("message_id", sa.String(length=200), nullable=False),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("sender_name", sa.String(length=200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("has_media", sa.Boolean(), nullable=False),
        sa.Column("property_slug", sa.String(length=200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "channel", "chat_id", "message_id", name="uq_chat_messages_message"
        ),
    )
    op.create_index(
        op.f("ix_chat_messages_account_id"), "chat_messages", ["account_id"], unique=False
    )
    op.create_index(
        "ix_chat_messages_conversation", "chat_messages",
        ["account_id", "channel", "chat_id", "sent_at"], unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for stmt in policy_statements("chat_messages"):
            op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables={"chat_messages"}):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index("ix_chat_messages_conversation", table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_account_id"), table_name="chat_messages")
    op.drop_table("chat_messages")
