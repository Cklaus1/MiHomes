"""create waitlist table

Revision ID: 0001_waitlist
Revises:
Create Date: 2026-07-31

Postgres-only, and the first revision of the landing tree. Unlike the 40 legacy
revisions in `alembic/` this uses plain `op.create_table` — their
`batch_alter_table` wrapper is a SQLite workaround (MULTITENANCY §5.4) and is not
needed here.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_waitlist"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "waitlist",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("num_homes", sa.String(10), nullable=True),
        sa.Column("has_staff", sa.Boolean(), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("utm_campaign", sa.String(200), nullable=True),
        sa.Column("utm_source", sa.String(200), nullable=True),
        sa.Column("utm_medium", sa.String(200), nullable=True),
        sa.Column("referred_by", sa.String(320), nullable=True),
        sa.Column("confirm_token_hash", sa.String(64), nullable=True),
        sa.Column("confirm_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirm_send_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signup_ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_waitlist_email", "waitlist", ["email"])
    op.create_index("ix_waitlist_email", "waitlist", ["email"])
    op.create_index("ix_waitlist_confirm_token_hash", "waitlist", ["confirm_token_hash"])
    # Supports the funnel metric: confirmed signups over a trailing window (GTM:293).
    op.create_index("ix_waitlist_confirmed_at", "waitlist", ["confirmed_at"])


def downgrade() -> None:
    op.drop_table("waitlist")
