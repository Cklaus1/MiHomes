"""email_preferences — each member's on/off switch per optional email (2026-11-23).

A tenant table with the standard RLS policy and drift-guard trigger, exactly like
`chat_notify_prefs` (0028). The household's team settings (which roles get which email) are
rows in `configurations`, so they need no schema.

Revision ID: 0034_email_preferences
Revises: 0033_ai_token_metering
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0034_email_preferences"
down_revision: Union[str, None] = "0033_ai_token_metering"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
_TABLE = "email_preferences"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", UUID, primary_key=True),
        sa.Column("account_id", UUID, sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("membership_id", UUID, sa.ForeignKey("memberships.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("email_type", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index(op.f(f"ix_{_TABLE}_account_id"), _TABLE, ["account_id"])
    op.create_index("uq_email_preferences_member_type", _TABLE,
                    ["account_id", "membership_id", "email_type"], unique=True)

    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for stmt in policy_statements(_TABLE):
            op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables={_TABLE}):
            op.execute(stmt)


def downgrade() -> None:
    # The index, policy and trigger drop with the table.
    op.drop_table(_TABLE)
