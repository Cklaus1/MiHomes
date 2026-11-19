"""add gcal_event_id to tasks

Revision ID: c3d4e5f6a7b8
Revises: 44d39e1593fd
Create Date: 2026-04-07

"""
import sqlalchemy as sa

from alembic import op

revision = 'c3d4e5f6a7b8'
down_revision = '44d39e1593fd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('gcal_event_id', sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column('tasks', 'gcal_event_id')
