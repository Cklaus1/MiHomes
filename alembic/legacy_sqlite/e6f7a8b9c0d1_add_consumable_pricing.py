"""add consumable pricing

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-05-22

"""
import sqlalchemy as sa

from alembic import op

revision = 'e6f7a8b9c0d1'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('consumables', sa.Column('unit_price', sa.Float, nullable=True))

    op.create_table(
        'consumable_price_entries',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('consumable_id', sa.Integer, sa.ForeignKey('consumables.id'), nullable=False),
        sa.Column('date', sa.Date, nullable=False),
        sa.Column('price', sa.Float, nullable=False),
        sa.Column('quantity', sa.Float, nullable=False, server_default='1.0'),
        sa.Column('entry_type', sa.String(50), nullable=False, server_default='purchase'),
        sa.Column('note', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_consumable_price_entries_consumable_id', 'consumable_price_entries', ['consumable_id'])


def downgrade() -> None:
    op.drop_index('ix_consumable_price_entries_consumable_id', 'consumable_price_entries')
    op.drop_table('consumable_price_entries')
    op.drop_column('consumables', 'unit_price')
