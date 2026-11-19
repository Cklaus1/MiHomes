"""add work_order vendor_name column

Revision ID: a8b9c0d1e2f3
Revises: f1e2d3c4b5a6
Create Date: 2026-05-13

"""
import sqlalchemy as sa

from alembic import op

revision = 'a8b9c0d1e2f3'
down_revision = 'f1e2d3c4b5a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('work_orders', sa.Column('vendor_name', sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column('work_orders', 'vendor_name')
