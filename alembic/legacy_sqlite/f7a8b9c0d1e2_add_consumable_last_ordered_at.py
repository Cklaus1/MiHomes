"""add consumable last_ordered_at

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-05-22

"""
import sqlalchemy as sa

from alembic import op

revision = 'f7a8b9c0d1e2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('consumables', sa.Column('last_ordered_at', sa.Date, nullable=True))


def downgrade() -> None:
    op.drop_column('consumables', 'last_ordered_at')
