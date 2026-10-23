"""gateway_bootstrap_policies — let the chat webhook find a sender's account under RLS (2026-10-23).

The Telegram webhook resolves a sender (`identity.resolve_sender`) and redeems `/link CODE`
(`linking.redeem_link_token`) before any account is bound — discovering the account is what those
reads do. They relied on a "Core select is unscoped" carve-out that only worked as a superuser.
Under the production role, which owns these tables with FORCE ROW LEVEL SECURITY, they saw zero
rows: every linked sender read as unlinked and every code as invalid.

Two SELECT-only policies, each keyed on a transaction-local GUC stamped by
`tenancy.connection.bind_gateway_guc`, each matching rows for exactly one external id. Mirrors
`rls.GATEWAY_BOOTSTRAP_POLICIES`, written out literally because a migration is a fixed point in
history and must not import a registry that keeps changing (see `drift_guard.py`).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0024_gateway_bootstrap_policies"
down_revision: Union[str, None] = "0023_playbook_checks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_POLICIES = (
    (
        "telegram_links",
        "telegram_links_gateway_sender",
        "telegram_user_id = (SELECT NULLIF(current_setting('app.gateway_telegram_user', true), '')"
        "::bigint)",
    ),
    (
        "gateway_link_tokens",
        "gateway_link_tokens_by_hash",
        "token_hash = (SELECT NULLIF(current_setting('app.gateway_link_token_hash', true), ''))",
    ),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, name, predicate in _POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
        op.execute(f"CREATE POLICY {name} ON {table} FOR SELECT USING ({predicate})")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, name, _predicate in _POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
