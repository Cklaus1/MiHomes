"""chat notifications (SPEC-014) — push_subscriptions, chat_notify_prefs, last_notified_seq.

Mirrors `models/chat_notify.py`; `test_pg_baseline::test_baseline_matches_metadata` fails on drift.
Every index leads with account_id (RLS), so the per-membership keys are (account_id, membership_id…).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0028_chat_notifications"
down_revision: Union[str, None] = "0027_native_chat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
_TABLES = ("push_subscriptions", "chat_notify_prefs")


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("membership_id", UUID, sa.ForeignKey("memberships.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(200), nullable=False),
        sa.Column("auth", sa.String(100), nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("account_id", "membership_id", "endpoint",
                            name="uq_push_subscriptions_member_endpoint"),
    )
    op.create_table(
        "chat_notify_prefs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("membership_id", UUID, sa.ForeignKey("memberships.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("email_digest", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("push", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("digest_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    for table in _TABLES:
        op.create_index(op.f(f"ix_{table}_account_id"), table, ["account_id"])
    op.create_index("uq_chat_notify_prefs_member", "chat_notify_prefs",
                    ["account_id", "membership_id"], unique=True)

    op.add_column("chat_participants", sa.Column(
        "last_notified_seq", sa.BigInteger(), server_default="0", nullable=False))

    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for table in _TABLES:
            for stmt in policy_statements(table):
                op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables=set(_TABLES)):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_column("chat_participants", "last_notified_seq")
    # Indexes, policies and triggers drop with their table.
    for table in reversed(_TABLES):
        op.drop_table(table)
