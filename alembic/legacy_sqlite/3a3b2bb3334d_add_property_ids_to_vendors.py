"""add property_ids to vendors

Revision ID: 3a3b2bb3334d
Revises: 2f14cedc92e8
Create Date: 2026-05-29 09:56:15.347007

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '3a3b2bb3334d'
down_revision: Union[str, None] = '2f14cedc92e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('vendors', sa.Column('property_ids', sa.JSON(), nullable=True))
    # Seed all existing vendors with Belle Estate (id=1)
    conn = op.get_bind()
    belle = conn.execute(sa.text("SELECT id FROM properties WHERE slug='belle-estate' LIMIT 1")).fetchone()
    if belle:
        conn.execute(sa.text("UPDATE vendors SET property_ids = :ids"), {"ids": f"[{belle[0]}]"})


def downgrade() -> None:
    op.drop_column('vendors', 'property_ids')
