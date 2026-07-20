"""add vendor_name and work_order_id to transactions

Revision ID: f9a0b1c2d3e4
Revises: c9d0e1f2a3b4
Create Date: 2026-05-14

"""
from alembic import op
import sqlalchemy as sa

revision = 'f9a0b1c2d3e4'
down_revision = 'c9d0e1f2a3b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('transactions', sa.Column('vendor_name', sa.String(300), nullable=True))
    op.add_column('transactions', sa.Column('work_order_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('transactions', 'work_order_id')
    op.drop_column('transactions', 'vendor_name')
