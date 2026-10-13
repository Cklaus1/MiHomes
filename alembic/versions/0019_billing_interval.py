"""Billing interval — whether a subscription is paid monthly or yearly.

One nullable column on `accounts`, additive, no backfill. The plan page needs it to show what the
customer pays and to offer a monthly subscriber the switch to yearly. Written only by
`apply_subscription_state` (webhook + reconcile), from the same price-id lookup that yields the
plan; `mihomes jobs reconcile` fills it for existing subscriptions.

Revision ID: 0019_billing_interval
Revises: 0018_account_lifecycle
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0019_billing_interval"
down_revision: Union[str, None] = "0018_account_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("billing_interval", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "billing_interval")
