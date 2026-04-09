"""add asset lifecycle fields

Revision ID: 2d292ff447e7
Revises: ee3e2a1404e7
Create Date: 2026-04-09

"""
from alembic import op
import sqlalchemy as sa

revision = '2d292ff447e7'
down_revision = 'ee3e2a1404e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('assets', sa.Column('install_date', sa.Date(), nullable=True))
    op.add_column('assets', sa.Column('expected_lifespan_years', sa.Float(), nullable=True))
    op.add_column('assets', sa.Column('replacement_cost_estimate', sa.Float(), nullable=True))
    op.add_column('assets', sa.Column('last_serviced', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('assets', 'last_serviced')
    op.drop_column('assets', 'replacement_cost_estimate')
    op.drop_column('assets', 'expected_lifespan_years')
    op.drop_column('assets', 'install_date')
