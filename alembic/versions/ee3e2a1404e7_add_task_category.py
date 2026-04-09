"""add task category

Revision ID: ee3e2a1404e7
Revises: e6b97b5b4e22
Create Date: 2026-04-09

"""
from alembic import op
import sqlalchemy as sa

revision = 'ee3e2a1404e7'
down_revision = 'e6b97b5b4e22'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('category', sa.String(50), nullable=True))
    op.create_index('ix_tasks_category', 'tasks', ['category'])


def downgrade() -> None:
    op.drop_index('ix_tasks_category', table_name='tasks')
    op.drop_column('tasks', 'category')
