"""calendar_connections + calendar_event_links — per-person Google Calendar sync (2026-10-30).

Mirrors `models/calendar_connection.py`; `test_pg_baseline::test_baseline_matches_metadata` fails
on drift. Tenant-owned: RLS policy and drift-guard trigger on both.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0029_calendar_connections"
down_revision: Union[str, None] = "0028_chat_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
_TABLES = ("calendar_connections", "calendar_event_links")


def upgrade() -> None:
    op.create_table(
        "calendar_connections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("membership_id", UUID, sa.ForeignKey("memberships.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("google_email", sa.String(320), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("calendar_id", sa.String(300), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("syncing_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("needs_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_table(
        "calendar_event_links",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("connection_id", UUID,
                  sa.ForeignKey("calendar_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("google_event_id", sa.String(1024), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("account_id", "connection_id", "entity_type", "entity_id",
                            name="uq_calendar_event_links_entity"),
    )
    for table in _TABLES:
        op.create_index(op.f(f"ix_{table}_account_id"), table, ["account_id"])
    op.create_index("uq_calendar_connections_member", "calendar_connections",
                    ["account_id", "membership_id"], unique=True)

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
    # Indexes, policies and triggers drop with their table.
    for table in reversed(_TABLES):
        op.drop_table(table)
