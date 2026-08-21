"""staff.user_id — SPEC-003 U6, the link `staff.view_own` needs to exist.

§4.1 classifies `staff` as `PERSONNEL` with the rule *"Staff may see their own record; never
others'"* — and no matrix key expressed that, so G6 declared every HR route `member.manage` and
staff saw **nothing**, including their own record. Fail-closed and stricter than the spec, recorded
at the time as U6.

**The blocker was never the key, it was the join.** "Their own record" requires knowing which
`staff` row belongs to the signed-in person, and nothing connected the two. `Staff.email` exists,
but matching on it is a soft link three ways: the column is nullable (so a NULL would match another
NULL), two rows may share an address, and an HR contact address is frequently not the address
someone signs in with. The only member→staff resolution in the codebase is
`review_common.resolve_reporter_by_name` — a fuzzy `ILIKE` on the *name*, written for attributing
inbound messages and explicitly a best-effort guess. Shipping a permission on top of either would
be shipping a rule that cannot be enforced correctly.

`ON DELETE SET NULL`, not CASCADE. The reference points from a **tenant** row (`staff`) at a
**global** one (`users`), so cascading would delete an employment record because a person deleted
their login — losing HR history to an unrelated action. `0006_user_last_used_account` made the same
call in the opposite direction and for the same reason: nulling drops the association and keeps
both rows.

**Nullable by design, and it stays that way.** Most `staff` rows are people with no MiHomes login
at all — a gardener, a contractor's crew — and the field is meaningless for them. A NOT NULL column
here would force a fake user per staff member.

No index: on a tenant table `index=True` emits `ix_staff_user_id = (user_id)`, which SPEC-002's
Step 3 rule rejects for not leading with `account_id`, and claiming an `EXPECTED_NON_LEADING`
exemption for an index nothing needs yet would spend the exemption list's credibility on nothing.
The lookup is one row by a signed-in user's id, against a table holding tens of rows per account.

No RLS policy and no drift-guard trigger, because neither applies: the table already has both from
`0001_pg_baseline`, and `drift_guard.parent_links()` skips foreign keys whose parent is in
`GLOBAL_TABLES` — `users` is global, so there is no `account_id` on the far side to keep consistent.

Revision ID: 0008_staff_user_id
Revises: 0007_telegram_links
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_staff_user_id"
down_revision: Union[str, None] = "0007_telegram_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "staff",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_staff_user",
        "staff",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_staff_user", "staff", type_="foreignkey")
    op.drop_column("staff", "user_id")
