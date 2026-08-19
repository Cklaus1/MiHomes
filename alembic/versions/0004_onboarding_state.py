"""onboarding_state — SPEC-003 §4.2, A17.

Records which onboarding steps an account has completed, so a user who drops off after step 2
resumes at step 3 rather than starting over (`ONBOARDING` §5).

`account_id` is both the primary key and the foreign key: one row per account, and the row is
unreachable without naming the account. `ON DELETE CASCADE` means deleting an account takes its
onboarding state with it rather than leaving an orphan keyed to a dead id.

Revision ID: 0004_onboarding_state
Revises: 0003_documents_staff_visible
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_onboarding_state"
down_revision: Union[str, None] = "0003_documents_staff_visible"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "onboarding_state",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "completed_steps",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
    )
    # RLS for the table this migration creates. `0002_rls` ran before `onboarding_state` existed,
    # so it could not have applied a policy — and every table in `TENANT_TABLES` must have one
    # (SPEC-002 A8). Applying it here is what keeps that invariant true as the registry grows:
    # the migration that creates a tenant table owns its policy.
    if op.get_bind().dialect.name == "postgresql":
        from mihomes.tenancy.rls import policy_statements

        for stmt in policy_statements("onboarding_state"):
            op.execute(stmt)

    # **No separate index on `account_id`**, unlike every other tenant table. `TenantOwned`'s
    # `@declared_attr` sets `index=True`, but this model overrides the column to make it the
    # primary key — and the override replaces the mixin's definition entirely, so the model
    # declares no index. Postgres already provides one for the primary key, so adding a second
    # would be redundant *and* would make `alembic check` report drift, which is how this was
    # caught: the first version created it and autogenerate immediately asked to remove it.


def downgrade() -> None:
    op.drop_table("onboarding_state")
