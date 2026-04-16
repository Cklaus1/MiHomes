"""add_task_estimated_hours

Revision ID: a1b2c3d4e5f6
Revises: 9fd3e984804e
Create Date: 2026-04-06 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9fd3e984804e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('estimated_hours', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('tasks', 'estimated_hours')
