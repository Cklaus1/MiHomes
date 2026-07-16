"""add recurring expense interval count

Revision ID: 089a9512f7ac
Revises: 4018a046502e
Create Date: 2026-07-09 13:52:26.999691

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '089a9512f7ac'
down_revision: Union[str, None] = '4018a046502e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('recurring_expenses') as batch_op:
        batch_op.add_column(sa.Column('interval_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('recurring_expenses') as batch_op:
        batch_op.drop_column('interval_count')
