"""add contract billing_frequency

Revision ID: e5a8fc64387d
Revises: 7c00f0bfbf49
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5a8fc64387d'
down_revision = '7c00f0bfbf49'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('contracts') as batch_op:
        batch_op.add_column(sa.Column('billing_frequency', sa.String(20), nullable=True))


def downgrade():
    with op.batch_alter_table('contracts') as batch_op:
        batch_op.drop_column('billing_frequency')
