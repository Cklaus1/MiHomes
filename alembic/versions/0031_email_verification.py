"""Email verification at signup (2026-11-13).

`users.email_verified_at` + `email_verification_tokens`. The table is GLOBAL, with no RLS policy
and no drift-guard trigger, for `password_reset_tokens`' reason: it is read before sign-in.

**Every existing user is backfilled as verified** (to `created_at`). Google users were always
verified by Google. Password users from before this point have been signing in for weeks, and
un-verifying them would lock out the whole current user base on deploy for no gain.

Revision ID: 0031_email_verification
Revises: 0030_calendar_import
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0031_email_verification"
down_revision: Union[str, None] = "0030_calendar_import"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE users SET email_verified_at = created_at WHERE email_verified_at IS NULL")

    op.create_table(
        "email_verification_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # sha256 hex of the raw token — the raw value is emailed once and never stored.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_email_verification_tokens_user_id"),
        "email_verification_tokens",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_verification_tokens_token_hash"),
        "email_verification_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_email_verification_tokens_token_hash"), table_name="email_verification_tokens"
    )
    op.drop_index(
        op.f("ix_email_verification_tokens_user_id"), table_name="email_verification_tokens"
    )
    op.drop_table("email_verification_tokens")
    op.drop_column("users", "email_verified_at")
