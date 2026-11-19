"""gateway_link_tokens — the only new table in SPEC-006 (§4.1, §4.2, Step 1, A3).

An ordinary tenant table: RLS policy and drift-guard trigger emitted, the same way `0015` does
it, via `policy_statements` / `trigger_ddl_statements` rather than hand-written DDL — so the
policy this table gets is the policy every other tenant table gets, and a change to that shape
reaches all of them at once.

## Three deviations from §4.2's DDL, each measured

1. **`postgresql.UUID(as_uuid=True)`, not `sa.String(36)`.** Every SPEC-003+ table in this tree
   uses UUID columns, and `memberships.id` is one — a `String(36)` FK would not build. The
   harness records this at §0.6 C8; N9's spirit is to follow the shipped pattern.

2. **The `membership_id` FK is created here. §4.2 omits it entirely.** Without it there is no
   `ondelete="CASCADE"`, and A10 — *"revoking a membership removes its gateway link with no
   extra code"* — would fall to application code to remember. `telegram_links` already makes
   that promise structural; a pending code must not outlive the membership it would bind to,
   or redeeming it would resurrect access that was deliberately revoked.

3. **No `EXPECTED_NON_LEADING` entry accompanies `uq_gateway_link_token_hash`,** which the
   harness's C8 predicted. Measured: a `UniqueConstraint` emits a *constraint*, not an index,
   so `test_tenant_indexes._tenant_indexes()` — which iterates `table.indexes` — never sees it,
   and the entry would be stale on arrival and fail
   `test_every_declared_exception_still_exists`. The invite precedent needed one only because
   `invite.py:43` declares `unique=True, index=True`, which does emit an index.

## Why the unique is on `token_hash` alone

Redemption looks a token up **before any account is known** — that lookup is how the account is
discovered (§4.2's carve-out, the same argument `ix_telegram_links_lookup` already won). A
composite `(account_id, token_hash)` would leave the only query this table exists to serve
unindexed, and would additionally let two accounts mint the same hash. Isolation is unaffected:
the row still carries `account_id`, and RLS still applies to every scoped read.

Revision ID: 0016_gateway_link_tokens
Revises: 0015_phase4_lifecycle
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_gateway_link_tokens"
down_revision: Union[str, None] = "0015_phase4_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("gateway_link_tokens",)


def upgrade() -> None:
    op.create_table(
        "gateway_link_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        # CASCADE from the membership: deviation 2 above. This is what makes A10 structural.
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        # "telegram" | "whatsapp" as a plain String, not an enum — the same shape
        # `memberships.role` uses. A new gateway should not need a migration to add a value.
        sa.Column("gateway", sa.String(20), nullable=False),
        # sha256 hex. The raw code is never written here (A4).
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_by_sender", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["membership_id"], ["memberships.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_gateway_link_token_hash"),
    )
    op.create_index(
        op.f("ix_gateway_link_tokens_account_id"),
        "gateway_link_tokens",
        ["account_id"],
        unique=False,
    )

    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for table in TABLES:
            for stmt in policy_statements(table):
                op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables=set(TABLES)):
            op.execute(stmt)


def downgrade() -> None:
    op.drop_index(
        op.f("ix_gateway_link_tokens_account_id"), table_name="gateway_link_tokens"
    )
    op.drop_table("gateway_link_tokens")
