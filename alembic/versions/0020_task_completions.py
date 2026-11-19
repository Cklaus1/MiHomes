"""task_completions + tasks.archived_at — repeating tasks reset in place (2026-10-16).

Completing a repeating task used to mark the row COMPLETED and create a brand-new task (and a new
schedule) for the next occurrence, so the task list grew by one row per completion forever. Now
the SAME task rolls forward to its next due date and goes back to pending; `task_completions`
records each time it was done, which is what reports, staff stats and "last done" read.

`property_id` is copied from the task rather than reached through it. query_scope documents that a
child table with no `property_id` is only protected when loaded through its parent; carrying the
column makes this table filterable for staff on its own.

`tasks.archived_at` lets a user clear a completed one-time task off the list without deleting it.

`completed_at` / `archived_at` are naive `DateTime`, matching `tasks.completed_at` and
`TimestampMixin` — `test_baseline_matches_metadata` compares types (see 0009's note).

Revision ID: 0020_task_completions
Revises: 0019_billing_interval
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020_task_completions"
down_revision: Union[str, None] = "0019_billing_interval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("archived_at", sa.DateTime(), nullable=True))

    op.create_table(
        "task_completions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("completed_by_staff_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("rolled_to", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"]),
        sa.ForeignKeyConstraint(["completed_by_staff_id"], ["staff.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # `TenantOwned.account_id` is declared `index=True`, so the model expects this single-column
    # index too (0009 creates its counterpart the same way).
    op.create_index(
        op.f("ix_task_completions_account_id"), "task_completions", ["account_id"], unique=False
    )
    op.create_index(
        "ix_task_completions_account_task", "task_completions", ["account_id", "task_id"]
    )
    op.create_index(
        "ix_task_completions_account_completed",
        "task_completions", ["account_id", "completed_at"],
    )

    # RLS and drift guard for the table this migration creates (0007/0009 established this).
    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for stmt in policy_statements("task_completions"):
            op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables={"task_completions"}):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index("ix_task_completions_account_completed", table_name="task_completions")
    op.drop_index("ix_task_completions_account_task", table_name="task_completions")
    op.drop_index(op.f("ix_task_completions_account_id"), table_name="task_completions")
    op.drop_table("task_completions")
    op.drop_column("tasks", "archived_at")
