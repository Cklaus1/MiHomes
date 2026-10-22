"""playbook_checks — shared tick boxes on a playbook's steps (2026-10-22).

A playbook is a document, not a task template: its `- [ ]` lines render as tick boxes people tick
while they read it. One row per ticked step, shared by the whole account (user decision), so the
housekeeper's tick is visible to the manager, with who and when. Unticking deletes the row.

`UNIQUE (playbook_id, item_key)` is what makes two people ticking the same step at once harmless:
the second insert collides and is ignored, and its index also serves lookups by playbook (no
separate `playbook_id` index: the tenant-index rule wants every index to lead with `account_id`).
Cascades with the playbook (and its account).

Revision ID: 0023_playbook_checks
Revises: 0022_playbooks
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023_playbook_checks"
down_revision: Union[str, None] = "0022_playbooks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "playbook_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_key", sa.String(length=40), nullable=False),
        sa.Column("checked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["playbook_id"], ["playbooks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["checked_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("playbook_id", "item_key", name="uq_playbook_checks_playbook_item"),
    )
    op.create_index(
        op.f("ix_playbook_checks_account_id"), "playbook_checks", ["account_id"], unique=False
    )

    # RLS and drift guard for the table this migration creates (0007/0009 established this).
    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for stmt in policy_statements("playbook_checks"):
            op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables={"playbook_checks"}):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index(op.f("ix_playbook_checks_account_id"), table_name="playbook_checks")
    op.drop_table("playbook_checks")
