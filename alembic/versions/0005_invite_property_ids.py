"""invites.property_ids — SPEC-003 D3 / A21.

§5 specifies `create_invite(session, account, inviter, email, role, property_ids)`, and D3
requires a staff invite with **zero** properties to be rejected outright ("fail closed, never
'all'"). Both need the scope set to survive from creation until acceptance — the invitee may not
sign up for days, and the inviter is not present to re-state it — but SPEC-002's `invites` table
has no column for it. This adds one.

`server_default '[]'` so existing rows (there are none in production, but the CLI and test
databases rebuild through this chain) are a well-formed empty list rather than NULL: the
acceptance path iterates it, and a NULL would be a crash rather than a fail-closed refusal.

Revision ID: 0005_invite_property_ids
Revises: 0004_onboarding_state
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_invite_property_ids"
down_revision: Union[str, None] = "0004_onboarding_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invites",
        sa.Column(
            "property_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("invites", "property_ids")
