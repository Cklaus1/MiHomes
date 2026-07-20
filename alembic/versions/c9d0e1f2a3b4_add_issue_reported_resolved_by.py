"""add issue reported_by_id and resolved_by_id columns

Revision ID: c9d0e1f2a3b4
Revises: a8b9c0d1e2f3
Create Date: 2026-05-14

"""
from alembic import op
import sqlalchemy as sa

revision = 'c9d0e1f2a3b4'
down_revision = 'a8b9c0d1e2f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('issues', sa.Column('reported_by_id', sa.Integer(), nullable=True))
    op.add_column('issues', sa.Column('resolved_by_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('issues', 'resolved_by_id')
    op.drop_column('issues', 'reported_by_id')
