"""Account lifecycle — cancel at period end, deactivation.

Two nullable columns on `accounts`, both additive, no backfill:

* `cancel_at_period_end` — the owner pressed Cancel on a paid subscription. Stripe keeps the
  subscription `active` until the period ends (so entitlements stay Pro, as `PRICING` §4.4
  requires), which means the status alone cannot tell the billing page to say "Pro until 23 Oct,
  then Free". Written by `apply_subscription_state` (webhook + reconcile) and by the cancel/undo
  routes. NOT NULL with a false default so every existing row reads "not cancelling".
* `deactivated_at` — the owner deactivated the household. Signing in is refused for everyone
  but the owner, who is offered "Reactivate". Also the clock for the 3-month rule: an account
  deactivated for over 90 days is permanently deleted by `mihomes jobs purge-deletions`.

Revision ID: 0018_account_lifecycle
Revises: 0017_email_password_auth
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0018_account_lifecycle"
down_revision: Union[str, None] = "0017_email_password_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "accounts",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "deactivated_at")
    op.drop_column("accounts", "cancel_at_period_end")
