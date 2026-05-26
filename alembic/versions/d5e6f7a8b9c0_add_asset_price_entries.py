"""add asset price entries

Revision ID: d5e6f7a8b9c0
Revises: c1d2e3f4a5b6
Create Date: 2026-05-22

"""
from alembic import op
import sqlalchemy as sa

revision = 'd5e6f7a8b9c0'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'asset_price_entries',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('asset_id', sa.Integer, sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('date', sa.Date, nullable=False),
        sa.Column('price', sa.Float, nullable=False),
        sa.Column('quantity', sa.Float, nullable=False, server_default='1.0'),
        sa.Column('entry_type', sa.String(50), nullable=False, server_default='purchase'),
        sa.Column('note', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_asset_price_entries_asset_id', 'asset_price_entries', ['asset_id'])

    # Backfill existing assets that have purchase_price set
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, purchase_price, purchase_date FROM assets WHERE purchase_price IS NOT NULL")
    ).fetchall()
    for row in rows:
        entry_date = row.purchase_date if row.purchase_date else '2026-01-01'
        connection.execute(
            sa.text(
                "INSERT INTO asset_price_entries (asset_id, date, price, quantity, entry_type) "
                "VALUES (:aid, :d, :p, 1.0, 'purchase')"
            ),
            {"aid": row.id, "d": entry_date, "p": row.purchase_price},
        )


def downgrade() -> None:
    op.drop_index('ix_asset_price_entries_asset_id', 'asset_price_entries')
    op.drop_table('asset_price_entries')
