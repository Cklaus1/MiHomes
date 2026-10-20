"""Drop the SOP document type — playbooks have their own page (2026-10-20).

"Playbook / SOP" was only a Documents label; the Playbooks page reads `knowledge/playbooks/` and
never touched these rows. Existing SOP documents become OTHER, so nothing is lost.

Postgres cannot drop a value from an enum, so the type is rebuilt: rename the old one, create the
new one without SOP, retype the column, drop the old type.

**The SOP → OTHER conversion is in the `USING` clause, not a separate UPDATE.** `documents` has
`FORCE ROW LEVEL SECURITY` (tenancy/rls.py), which applies to the table owner — the role that
runs migrations in some deployments. With no tenant GUC set, an owner's UPDATE would see zero
rows and silently convert nothing. A column rewrite is DDL and covers every row regardless.

Downgrade restores the type with SOP but cannot know which OTHER rows used to be SOP.

Revision ID: 0021_drop_sop_document_type
Revises: 0020_task_completions
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0021_drop_sop_document_type"
down_revision: Union[str, None] = "0020_task_completions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WITHOUT_SOP = (
    "'WARRANTY', 'CONTRACT', 'INVOICE', 'MANUAL', 'INSURANCE', 'PERMIT', 'REGULATION', "
    "'REPORT', 'PHOTO', 'OTHER'"
)
_WITH_SOP = (
    "'WARRANTY', 'CONTRACT', 'INVOICE', 'MANUAL', 'INSURANCE', 'PERMIT', 'REGULATION', 'SOP', "
    "'REPORT', 'PHOTO', 'OTHER'"
)


def _rebuild(values: str, using: str) -> None:
    op.execute("ALTER TYPE documenttype RENAME TO documenttype_old")
    op.execute(f"CREATE TYPE documenttype AS ENUM ({values})")
    op.execute(
        f"ALTER TABLE documents ALTER COLUMN document_type TYPE documenttype USING ({using})::documenttype"
    )
    op.execute("DROP TYPE documenttype_old")


def upgrade() -> None:
    _rebuild(
        _WITHOUT_SOP,
        "CASE WHEN document_type::text = 'SOP' THEN 'OTHER' ELSE document_type::text END",
    )


def downgrade() -> None:
    _rebuild(_WITH_SOP, "document_type::text")
