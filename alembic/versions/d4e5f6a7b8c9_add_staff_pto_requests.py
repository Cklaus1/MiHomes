"""add staff_pto_requests table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-07

"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'staff_pto_requests',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('staff_id', sa.Integer, sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('dates', sa.JSON, nullable=False),
        sa.Column('status', sa.Enum('pending', 'approved', 'denied', name='ptostatus'), nullable=False, server_default='pending'),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('coverage_warning', sa.Text, nullable=True),
        sa.Column('decided_at', sa.DateTime, nullable=True),
        sa.Column('decided_by', sa.String(200), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('staff_pto_requests')
