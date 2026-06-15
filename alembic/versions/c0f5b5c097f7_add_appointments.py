"""add appointments table

Revision ID: c0f5b5c097f7
Revises: e5a8fc64387d
Create Date: 2026-06-15
"""
import sqlalchemy as sa
from alembic import op

revision = 'c0f5b5c097f7'
down_revision = 'e5a8fc64387d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'appointments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('property_id', sa.Integer(), sa.ForeignKey('properties.id'), nullable=False),
        sa.Column('vendor_id', sa.Integer(), sa.ForeignKey('vendors.id'), nullable=True),
        sa.Column('contract_id', sa.Integer(), sa.ForeignKey('contracts.id'), nullable=True),
        sa.Column('date', sa.Date(), nullable=False, index=True),
        sa.Column('start_time', sa.Time(), nullable=True),
        sa.Column('appointment_type', sa.String(50), nullable=False, server_default='vendor_visit'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('gcal_event_id', sa.String(200), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table('appointments')
