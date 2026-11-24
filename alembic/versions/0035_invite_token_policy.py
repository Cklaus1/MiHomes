"""invite_token_policy — let an invitee's link find its invitation under RLS (2026-11-18).

`invite_service.find_pending` / `accept_invite` look an invitation up by token hash before any
account is bound; discovering the account is what that read does. Under the production role
(FORCE ROW LEVEL SECURITY) the unscoped read saw zero rows, so every valid invite link showed
"This invitation is no longer valid". Same defect and same fix as 0024's gateway policies: one
SELECT-only policy, keyed on a transaction-local GUC, matching exactly one token hash.

Mirrors `rls.GATEWAY_BOOTSTRAP_POLICIES["invites"]`, written out literally because a migration is
a fixed point in history and must not import a registry that keeps changing.

Revision ID: 0035_invite_token_policy
Revises: 0034_email_preferences
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0035_invite_token_policy"
down_revision: Union[str, None] = "0034_email_preferences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAME = "invites_by_token_hash"
_PREDICATE = "token_hash = (SELECT NULLIF(current_setting('app.invite_token_hash', true), ''))"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP POLICY IF EXISTS {_NAME} ON invites")
    op.execute(f"CREATE POLICY {_NAME} ON invites FOR SELECT USING ({_PREDICATE})")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP POLICY IF EXISTS {_NAME} ON invites")
