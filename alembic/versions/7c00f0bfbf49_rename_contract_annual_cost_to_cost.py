"""rename contract annual_cost to cost

Revision ID: 7c00f0bfbf49
Revises: 3a3b2bb3334d
Create Date: 2026-06-12
"""
from alembic import op

revision = '7c00f0bfbf49'
down_revision = '3a3b2bb3334d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('contracts') as batch_op:
        batch_op.alter_column('annual_cost', new_column_name='cost')


def downgrade():
    with op.batch_alter_table('contracts') as batch_op:
        batch_op.alter_column('cost', new_column_name='annual_cost')
