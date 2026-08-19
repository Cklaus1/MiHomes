"""documents.staff_visible — SPEC-003 D13 / A14.

Closes F2c: §9.3's account-level carve-out names *"account-level vendors, budgets, account
settings"* and **not documents**, so nothing in the source resolved whether a staff member may
see an invoice attached to a work order they are scoped to. D13 answers it with a flag, owner and
admin controlled, defaulting to **false**.

**`server_default` is the point, not a formality.** A Python-side default would leave every row
inserted by raw SQL, by a later migration, or by the importer silently *visible* — and D13's
posture is that a document is hidden unless something positively authorises it. Existing rows are
backfilled to `false` for the same reason: this migration must not expose a single document that
was not exposed before it ran.

§3's manifest folds this into one `0003_phase2_rbac` file alongside `telegram_links`,
`onboarding_state`, and the audit change. Those land in four different groups, and one file
spanning four commits is not resumable — so each ships its own revision in the chain, per §6's
own "independently verifiable and separately committable".

Revision ID: 0003_documents_staff_visible
Revises: 0002_rls
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_documents_staff_visible"
down_revision: Union[str, None] = "0002_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "staff_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "staff_visible")
