"""add session_name to ai_conversations

Revision ID: 2f14cedc92e8
Revises: f7a8b9c0d1e2
Create Date: 2026-05-28 12:44:00.788951

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '2f14cedc92e8'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_conversations', sa.Column('session_name', sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_conversations', 'session_name')
