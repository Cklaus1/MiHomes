"""0002_rls — row-level security on every tenant table (SPEC-002 Step 7).

Generated from `mihomes.tenancy.rls`, not hand-written: 40 near-identical policy blocks
maintained by hand is how one table ends up without a policy. Same discipline as the G4
drift guard, and for the same reason — the DDL has exactly one definition, shared with the
`create_all` path the test suite uses, so the two cannot diverge.

**This migration does not create the `app` role, deliberately.** A role is cluster-wide
rather than per-database, so `CREATE ROLE` here would collide the second time the migration
ran against another database in the same cluster. Provisioning owns it. The production
equivalent of what `tests/conftest.py` does for tests is:

    CREATE ROLE mihomes_app LOGIN PASSWORD '...';   -- NOT superuser, NOT BYPASSRLS (N5)
    GRANT USAGE ON SCHEMA public TO mihomes_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mihomes_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mihomes_app;

`GRANT USAGE ON SCHEMA` is required and is **not** implied by the table grants — without it
every table reads as "relation does not exist" rather than "permission denied", which is a
confusing way to discover a missing grant.

**Migrations run as the owner and bypass these policies. That is correct and intended**
(N5): a migration has to touch every tenant's rows. The runtime role must not be an owner or
a `BYPASSRLS` role, and must never be a superuser — superusers bypass RLS even with `FORCE`,
measured on this cluster.
"""
from typing import Sequence, Union

from alembic import op

from mihomes.tenancy.rls import drop_statements, rls_statements

# revision identifiers, used by Alembic.
revision: str = '0002_rls'
down_revision: Union[str, None] = '0001_pg_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite has no RLS. The baseline is already dialect-guarded the same way; see its
    # docstring for what a SQLite-built database consequently lacks.
    if op.get_bind().dialect.name != "postgresql":
        return
    for stmt in rls_statements():
        op.execute(stmt)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for stmt in drop_statements():
        op.execute(stmt)
