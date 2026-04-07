"""add daily recurrence frequency

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-07

SQLite stores enums as VARCHAR, so no ALTER needed — the new value
is accepted automatically once the Python enum includes it.
"""
from alembic import op

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite uses VARCHAR for enums — no schema change needed.
    # Migrate the 6 "Daily:" tasks that were incorrectly stored as weekly.
    op.execute("""
        UPDATE task_schedules
        SET frequency = 'daily'
        WHERE task_id IN (
            SELECT id FROM tasks
            WHERE title LIKE 'Daily:%'
        )
        AND frequency = 'weekly'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE task_schedules
        SET frequency = 'weekly'
        WHERE task_id IN (
            SELECT id FROM tasks
            WHERE title LIKE 'Daily:%'
        )
        AND frequency = 'daily'
    """)
