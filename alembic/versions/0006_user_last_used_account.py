"""users.last_used_account_id — SPEC-003 D11 / A24.

D11: the account switcher *"updates `sessions.current_account_id` server-side and persists
`last_used_account`."* The session column already exists (SPEC-002 G12) and is per-session — so a
**new** session, on a new device or after a cleared cookie, has no account to open at. This column
is that default: the difference between signing in and landing where you were, versus signing in
and being asked which of your accounts you meant.

`ON DELETE SET NULL` rather than CASCADE. The reference points from a **global** row (`users`) at
a tenant root (`accounts`), so cascading would delete the *person* when an account is deleted —
and a user may be a member of several. Nulling drops the preference and keeps the human.

Revision ID: 0006_user_last_used_account
Revises: 0005_invite_property_ids
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_user_last_used_account"
down_revision: Union[str, None] = "0005_invite_property_ids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_used_account_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_last_used_account",
        "users",
        "accounts",
        ["last_used_account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_last_used_account", "users", type_="foreignkey")
    op.drop_column("users", "last_used_account_id")
