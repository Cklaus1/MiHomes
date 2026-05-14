"""add work order ai_report column

Revision ID: f1e2d3c4b5a6
Revises: ('9fd3e984804e', 'b7f83a21cc94', 'a1b2c3d4e5f6')
Create Date: 2026-05-12

"""
from alembic import op
import sqlalchemy as sa

revision = 'f1e2d3c4b5a6'
down_revision = ('9fd3e984804e', 'b7f83a21cc94', 'a1b2c3d4e5f6')
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('work_orders', sa.Column('ai_report', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('work_orders', 'ai_report')
