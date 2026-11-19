"""add issue_id to work_orders

Revision ID: 69147420df90
Revises: 50623b2a0000
Create Date: 2026-06-17
"""
import sqlalchemy as sa

from alembic import op

revision = '69147420df90'
down_revision = 'c0f5b5c097f7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('work_orders') as batch_op:
        batch_op.add_column(sa.Column('issue_id', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('work_orders') as batch_op:
        batch_op.drop_column('issue_id')
