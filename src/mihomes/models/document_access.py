"""`DocumentAccess` — per-person document grants. The owner decides who sees each document.

**This replaces `documents.staff_visible` as the owner-controlled gate**, and the reason is that
the flag was the wrong shape for what it was asked to do. D13 gave every document one boolean:
ticked, and *every* staff member in scope sees it; unticked, and none do. An estate has documents
that are appropriate for one person and not another — a contractor's crew rota, a specific
employee's paperwork — and a single flag cannot say so. Asking the owner to set two controls (tick
the flag *and* grant the person) to express one intention is how "why can't Ana see it?" happens,
so the grant is now the whole gate.

`staff_visible` is deliberately **left on the table and ignored** rather than dropped. It never had
a setter — no service argument, no route form field, nothing in the UI; the only writers were tests
using raw SQL — so no data depends on it and nothing regresses by disregarding it. Dropping the
column would mean a migration whose only effect is to remove a field a future decision might want
back, so the column stays and `query_scope` stops reading it. That is recorded in
`authz/query_scope.py::_document_criteria`, which is where a reader would look.

**Property scope still applies, on top of the grant.** A grant says "this person may see this
document"; §9.4's scoping says "and only for properties they cover". Both, ANDed — a grant is not
an escape hatch from G7, and the two conditions answer different questions.

## Why `staff_id` and not `membership_id`

`telegram_links` keys on `memberships` because its subject is a *capability* — N6's rule, that
`memberships.role` is the matrix's vocabulary while `StaffRole` is a job title containing its own
`OWNER`. This table's subject is different: the owner is choosing from the **people directory**,
which is `staff`. Keying on `staff` is what makes the picker a list of named people rather than of
membership rows, and the join to the signed-in user runs through `Staff.user_id` — the link U6a
added precisely so "which row is this person" has a hard answer.

The consequence, and it is a deliberate one: **a grant to a staff row with no `user_id` matches
nothing.** Such a person has no login, so no request can ever carry their identity. The picker
therefore offers only staff who have one, rather than letting the owner create a grant that looks
active and does nothing.

## Why an explicit table rather than a polymorphic one

The obvious generalisation is `access_grants(entity_type, entity_id, staff_id)`, covering documents
today and anything else later. U7 is the argument against it: the polymorphic models already in the
tree (`Note`, `TagAssignment`) each needed their own criteria branch, and a row whose `entity_type`
is unrecognised or whose `entity_id` is NULL has to be *explicitly* excluded or SQL's three-valued
logic decides its fate for you. A concrete FK to `documents.id` cannot express those states at all,
which is a cheaper guarantee than remembering to fail closed.
"""

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin


class DocumentAccess(Base, TimestampMixin, TenantOwned):
    __tablename__ = "document_access"
    __table_args__ = (
        # One grant per (document, person). Scoped to `account_id` like every other uniqueness
        # constraint in this schema (SPEC-002 Step 3): two accounts may each grant their own
        # document to their own staff member, and a global constraint would make the second
        # account's grant collide with the first.
        UniqueConstraint(
            "account_id", "document_id", "staff_id", name="uq_document_access_doc_staff"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    #: `CASCADE` on both sides, and both directions are intentional.
    #:
    #: Deleting a **document** must take its grants with it: a grant naming a row that no longer
    #: exists is unreachable clutter that a later `document_id` reuse could resurrect.
    #: Deleting a **staff member** must take their grants too — an ex-employee's access should not
    #: outlive their record, and this is the one place where cascading is the *safer* reading.
    #:
    #: Contrast `staff.user_id`, which is `SET NULL`: there the reference points from a tenant row
    #: at a **global** one, so cascading would delete an employment record because someone deleted
    #: their login. Here both sides are tenant-owned and both are the subject of the grant.
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Who granted it. Audit context the `audit_log` row also carries, kept here because "who
    #: gave this person access" is a question asked *of the grant*, and answering it from the
    #: audit trail means reconstructing state from events.
    #:
    #: Nullable and `SET NULL`: the granting user is GLOBAL (`users`), so the same reasoning as
    #: `staff.user_id` applies — deleting a login must not delete the grant it created.
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    document = relationship("Document", backref="access_grants")
    staff = relationship("Staff", backref="document_grants")
