"""add appointment recurring expense link and completed flag

Revision ID: 7b802030db83
Revises: 089a9512f7ac
Create Date: 2026-07-09 14:28:56.219635

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7b802030db83'
down_revision: Union[str, None] = '089a9512f7ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('appointments') as batch_op:
        batch_op.add_column(sa.Column('recurring_expense_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('completed', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('appointments') as batch_op:
        batch_op.drop_column('completed')
        batch_op.drop_column('recurring_expense_id')
