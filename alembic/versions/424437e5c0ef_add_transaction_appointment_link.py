"""add transaction appointment link

Revision ID: 424437e5c0ef
Revises: 7b802030db83
Create Date: 2026-07-09 14:31:16.725195

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '424437e5c0ef'
down_revision: Union[str, None] = '7b802030db83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('transactions') as batch_op:
        batch_op.add_column(sa.Column('appointment_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('transactions') as batch_op:
        batch_op.drop_column('appointment_id')
