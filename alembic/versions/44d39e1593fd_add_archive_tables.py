"""add_archive_tables

Revision ID: 44d39e1593fd
Revises: b2c3d4e5f6a7
Create Date: 2026-04-07 11:27:01.238730

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44d39e1593fd'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'audit_log_archive',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(10), nullable=False),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('actor', sa.String(100), nullable=True),
        sa.Column('archived_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_log_archive_timestamp', 'audit_log_archive', ['timestamp'])
    op.create_index('ix_audit_log_archive_entity_type', 'audit_log_archive', ['entity_type'])

    op.create_table(
        'ai_conversations_archive',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(50), nullable=False),
        sa.Column('role', sa.String(50), nullable=False),
        sa.Column('user_message', sa.Text(), nullable=False),
        sa.Column('ai_response', sa.Text(), nullable=False),
        sa.Column('context_summary', sa.Text(), nullable=True),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
        sa.Column('provider', sa.String(50), nullable=True),
        sa.Column('model', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('archived_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ai_conversations_archive_session_id', 'ai_conversations_archive', ['session_id'])


def downgrade() -> None:
    op.drop_index('ix_ai_conversations_archive_session_id', table_name='ai_conversations_archive')
    op.drop_table('ai_conversations_archive')
    op.drop_index('ix_audit_log_archive_entity_type', table_name='audit_log_archive')
    op.drop_index('ix_audit_log_archive_timestamp', table_name='audit_log_archive')
    op.drop_table('audit_log_archive')
