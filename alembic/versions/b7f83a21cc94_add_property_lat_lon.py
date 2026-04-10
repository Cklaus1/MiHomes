"""add_property_lat_lon

Revision ID: a1b2c3d4e5f6
Revises: fcd28cf78d5f
Create Date: 2026-04-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f83a21cc94'
down_revision: Union[str, None] = '2d292ff447e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('properties', sa.Column('latitude', sa.Float(), nullable=True))
    op.add_column('properties', sa.Column('longitude', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('properties') as batch_op:
        batch_op.drop_column('longitude')
        batch_op.drop_column('latitude')
