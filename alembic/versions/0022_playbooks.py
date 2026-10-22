"""playbooks + memberships.can_edit_playbooks — per-account, editable playbooks (2026-10-21).

Playbooks were markdown files in `knowledge/playbooks/`, read by every account, so one estate's
internal procedures were visible to every signup and nobody could edit them in the app. They are
now tenant rows behind the same RLS policy as every other tenant table. The existing files are
imported into their owner's account by `scripts/import_playbooks.py`, not here: no migration in
this tree targets one account, and a data load that depends on files in the checkout does not
belong in a schema step that must also run on a fresh database.

`memberships.can_edit_playbooks` lets an owner/admin hand playbook writing to chosen staff
without making them admins (user decision). `server_default false` so every existing member,
staff included, starts without it.

Revision ID: 0022_playbooks
Revises: 0021_drop_sop_document_type
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022_playbooks"
down_revision: Union[str, None] = "0021_drop_sop_document_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column("can_edit_playbooks", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "playbooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=True),
        sa.Column("audience", sa.String(length=200), nullable=True),
        sa.Column("estimated_time", sa.String(length=100), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "slug", name="uq_playbooks_account_slug"),
    )
    # `TenantOwned.account_id` is declared `index=True`, so the model expects this index too.
    op.create_index(op.f("ix_playbooks_account_id"), "playbooks", ["account_id"], unique=False)

    # RLS and drift guard for the table this migration creates (0007/0009 established this).
    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for stmt in policy_statements("playbooks"):
            op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables={"playbooks"}):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index(op.f("ix_playbooks_account_id"), table_name="playbooks")
    op.drop_table("playbooks")
    op.drop_column("memberships", "can_edit_playbooks")
