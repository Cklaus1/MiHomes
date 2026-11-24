"""AI usage is capped in tokens, not calls (2026-11-19).

Adds `ai_usage_rollups.tokens_used` (tokens in + out for the period). `calls_used` stays, as
telemetry. There is nothing to backfill: the event log's token columns were never filled, so the
current period starts counting tokens from zero at deploy.

This period's 80% / 100% warning markers are cleared, because they were set against call counts
and would otherwise stop the token-based warnings from firing until next period.

**Down-revision note:** there is no 0032. The `invite-email` branch's 0032 also revised 0031; it
was renumbered `0035_invite_token_policy` (after 0034) when merged, so the chain keeps one head.

Revision ID: 0033_ai_token_metering
Revises: 0031_email_verification
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0033_ai_token_metering"
down_revision: Union[str, None] = "0031_email_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_usage_rollups",
        sa.Column("tokens_used", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE ai_usage_rollups SET warned_80_at = NULL, warned_100_at = NULL "
        "WHERE period_end >= CURRENT_DATE"
    )


def downgrade() -> None:
    op.drop_column("ai_usage_rollups", "tokens_used")
