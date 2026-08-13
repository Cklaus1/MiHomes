"""add consumable low stock threshold

Revision ID: 4018a046502e
Revises: 69147420df90
Create Date: 2026-07-09 13:15:13.208294

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4018a046502e'
down_revision: Union[str, None] = '69147420df90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('consumables') as batch_op:
        batch_op.add_column(sa.Column('low_stock_threshold', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('consumables') as batch_op:
        batch_op.drop_column('low_stock_threshold')
