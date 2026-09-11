"""Role changes, owner transfer, offboarding — SPEC-003 §6 Step 14 (A22, A23).

**Against `memberships` and its partial unique index (SPEC-002 D4), never
`accounts.owner_user_id`** — B2: that column does not exist, and the spec says so twice because
`ONBOARDING` §§35/43/220 all reference it. Ownership is *"the partial unique index on
`memberships`"*: `UNIQUE (account_id) WHERE role = 'owner' AND status = 'active'`.

That index is the real guarantee, and it shapes the code below. It makes "two active owners"
unrepresentable, which is why `transfer_ownership` **demotes before it promotes** — the reverse
order violates the constraint mid-transaction and the database refuses it. Relying on the index
rather than on a check is the point: a check can be forgotten by the next call site, and this one
cannot.

**D2 — ownership moves only by transfer, never by invite or role change.** `change_role` refuses
to hand out `owner` at all; this module's `transfer_ownership` is the only path, and it is
atomic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mihomes.ids import new_id
from mihomes.models.membership import Membership, MembershipPropertyScope
from mihomes.models.property import Property
from mihomes.models.user import User
from mihomes.services.audit import record_change
from mihomes.tenancy import account_context
from mihomes.tenancy.connection import bind_account_guc

__all__ = [
    "MemberView",
    "MembershipError",
    "change_role",
    "list_members",
    "offboard",
    "property_scopes_by_membership",
    "set_property_scope",
    "transfer_ownership",
]

#: Owner, then admin, then staff. A rank rather than an alphabetical sort on the role string,
#: which would read admin/owner/staff and put the person who runs the estate in the middle.
_ROLE_ORDER = {"owner": 0, "admin": 1, "staff": 2}


@dataclass(frozen=True)
class MemberView:
    """One row of the members list — a read model, deliberately not a `Membership`.

    The template needs the person's name and email, which live on `users` (GLOBAL), alongside
    their role, which lives on `memberships` (tenant-owned). Handing the template two ORM
    objects to join in Jinja would put a query in the render path for every row; flattening it
    here keeps the page one query.
    """

    membership_id: uuid.UUID
    user_id: uuid.UUID
    name: str | None
    email: str
    role: str
    joined_at: datetime

    @property
    def display_name(self) -> str:
        """Their name, or the email that is all we have until they set one."""
        return self.name or self.email


class MembershipError(Exception):
    """A membership change was refused. Carries a caller-safe message."""


def _active_owner_count(session: Session, account_id: uuid.UUID) -> int:
    return session.execute(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.account_id == account_id,
            Membership.role == "owner",
            Membership.status == "active",
        )
    ).scalar_one()


def _is_last_active_owner(session: Session, membership: Membership) -> bool:
    """A22's predicate. Read fresh rather than inferred from the caller's view of the world."""
    if membership.role != "owner" or membership.status != "active":
        return False
    return _active_owner_count(session, membership.account_id) <= 1


def change_role(
    session: Session,
    actor: Membership,
    target: Membership,
    new_role: str,
) -> Membership:
    """Change a member's role, applying R1 — row 13's *"(not owner's, not own)"*.

    R1 lives in `authz/actions.py` as a function precisely because it cannot be expressed as a
    grant: it depends on who the target is relative to the actor. It is *called* here rather than
    reimplemented, so the rule the matrix tests assert is the rule that actually runs.

    **`owner` cannot be assigned** (D2). Ownership moves only through `transfer_ownership`, which
    is atomic; allowing it here would be a second, unaudited route to it — and the partial unique
    index would then reject whichever attempt lost the race, surfacing as a database error rather
    than as a policy decision.
    """
    from mihomes.authz.actions import EXTRA_RULES

    new_role = new_role.strip().lower()
    if new_role == "owner":
        raise MembershipError(
            "ownership moves only by transfer, never by role change (D2)"
        )
    if new_role not in ("admin", "staff"):
        raise MembershipError(f"unknown role {new_role!r}")

    if not EXTRA_RULES["R1"](
        actor_role=actor.role,
        actor_membership_id=actor.id,
        target_role=target.role,
        target_membership_id=target.id,
    ):
        raise MembershipError(
            "you may not change that member's role — an admin cannot change the owner's or "
            "their own, and nobody may change their own (R1)"
        )

    # A22 — demotion is a removal of the last owner by another name.
    #
    # **Currently unreachable through this function, and kept deliberately.** R1 above already
    # refuses every actor who could reach it: the owner demoting themselves ("nobody may change
    # their own"), an admin demoting them ("not the owner's"), and another owner is impossible
    # because two active owners cannot exist. This becomes the operative guard the moment R1 is
    # relaxed — which `ONBOARDING` §11 Q2's granular staff capabilities would do — and a guard
    # added *then* is a guard added after the hole. `offboard` reaches the same predicate on a
    # path R1 does not cover, which is where it is actually exercised.
    if _is_last_active_owner(session, target):
        raise MembershipError(
            "this account's only owner cannot be demoted; transfer ownership first"
        )

    target.role = new_role
    session.flush()
    return target


def offboard(session: Session, membership: Membership) -> Membership:
    """Revoke a membership. **The account keeps the work** (`ONBOARDING:225`).

    Soft revocation rather than deletion, and that is a data decision, not caution: tasks, notes,
    issues and uploads are *"owned by the account rather than the member"*, so deleting the row
    would either orphan them or cascade them away with the person who happened to file them.
    A housekeeper leaving must not take three years of maintenance history with her.

    A22 — the last active owner cannot be offboarded. An account with no owner is unreachable by
    anyone: nothing could invite, transfer, or delete it, and no support path exists to recover it.
    """
    if _is_last_active_owner(session, membership):
        raise MembershipError(
            "this account's only owner cannot be removed; transfer ownership first"
        )

    membership.status = "revoked"
    session.flush()
    return membership


def transfer_ownership(
    session: Session,
    from_membership: Membership,
    to_membership: Membership,
) -> tuple[Membership, Membership]:
    """Move ownership between two members of the same account — A23.

    **Demote before promote, and the order is the database's requirement, not a preference.**
    SPEC-002 D4's partial unique index makes two simultaneous active owners unrepresentable, so
    promoting first raises `IntegrityError` mid-transaction. Demoting first leaves the account
    momentarily ownerless *inside* the transaction, which nothing outside it can observe.

    The outgoing owner becomes an `admin` rather than being revoked: transferring ownership is
    not the same act as leaving, and conflating them would silently remove someone's access as a
    side effect of a handover.
    """
    if from_membership.account_id != to_membership.account_id:
        # Not a cross-account leak risk so much as a nonsense operation, but refusing loudly
        # beats writing two rows that disagree about which account they belong to.
        raise MembershipError("both memberships must belong to the same account")

    if from_membership.id == to_membership.id:
        raise MembershipError("cannot transfer ownership to the current owner")

    if from_membership.role != "owner" or from_membership.status != "active":
        raise MembershipError("only the active owner can transfer ownership")

    if to_membership.status != "active":
        raise MembershipError("cannot transfer ownership to a revoked member")

    from_membership.role = "admin"
    session.flush()          # the account now has zero owners, inside this transaction only
    to_membership.role = "owner"
    session.flush()          # ...and exactly one again

    return from_membership, to_membership


def list_members(session: Session, account_id: uuid.UUID) -> list[MemberView]:
    """Every active member of the account, as an org chart rather than an insertion log.

    Joins `users` — which is GLOBAL — to `memberships`, which is not. That crossing is the
    reason this reuses the shape of `billing/service.py::_billing_email` rather than inventing
    one: that query already reaches `User` fields through `Membership` and already filters on
    `status == "active"`, which matters here for the same reason it matters there. A revoked
    member keeps their row (`offboard` is a soft revoke, so the account keeps their work), so a
    query keyed only on `account_id` would list people who no longer have access.

    Ordered owner, then admin, then staff, alphabetical within each. Insertion order would put
    a housekeeper hired last at the bottom and the owner wherever they happened to land, which
    reads as a log; this reads as who runs the estate.
    """
    rows = session.execute(
        select(
            Membership.id,
            Membership.role,
            Membership.created_at,
            User.id.label("user_id"),
            User.name,
            User.email,
        )
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.account_id == account_id,
            Membership.status == "active",
        )
    ).all()

    return sorted(
        (
            MemberView(
                membership_id=row.id,
                user_id=row.user_id,
                name=row.name,
                email=row.email,
                role=row.role,
                joined_at=row.created_at,
            )
            for row in rows
        ),
        key=lambda m: (_ROLE_ORDER.get(m.role, len(_ROLE_ORDER)), (m.name or m.email).lower()),
    )


def set_property_scope(
    session: Session,
    membership: Membership,
    property_ids: list[uuid.UUID],
) -> None:
    """Replace a staff member's property whitelist wholesale.

    **Refuses an empty list**, the same refusal `create_invite` makes at creation time and for
    the same reason (A21/D3): zero scope rows means zero properties visible, and a member who
    can sign in and see nothing cannot tell that from the product being broken. The fail-closed
    direction of D3 means the fix cannot be "grant all", so the only safe answer is to refuse
    the write.

    **Refuses for owner and admin.** `scoped_property_ids` ignores their scope rows outright
    (`ONBOARDING:44`), so rows written here would never be read — state that looks like it
    governs access and does not. Storing it would invite exactly one bug: someone reads the
    rows back, believes they are the whitelist, and narrows an owner who is in fact unrestricted.

    Every id is checked against this account before anything is written, so a property id
    belonging to another estate cannot enter the whitelist.
    """
    if membership.role != "staff":
        raise MembershipError(
            f"a {membership.role} already sees every property, so a scope cannot be set for "
            "them — scope rows are read only for staff (ONBOARDING:44)"
        )

    requested = list(dict.fromkeys(property_ids))
    if not requested:
        raise MembershipError(
            "a staff member must keep at least one property — zero scope rows means zero "
            "properties visible, which is indistinguishable from a broken account (D3)"
        )

    with account_context(membership.account_id):
        bind_account_guc(session, membership.account_id)

        known = set(
            session.execute(
                select(Property.id).where(
                    Property.id.in_(requested),
                    Property.account_id == membership.account_id,
                )
            ).scalars()
        )
        missing = [p for p in requested if p not in known]
        if missing:
            # One message for "another account's" and "does not exist" alike — the same
            # reasoning D9 applies to targets, on a surface where the ids come from a form.
            raise MembershipError("that property is not part of this estate")

        session.execute(
            delete(MembershipPropertyScope).where(
                MembershipPropertyScope.membership_id == membership.id,
                MembershipPropertyScope.account_id == membership.account_id,
            )
        )
        for property_id in requested:
            session.add(
                MembershipPropertyScope(
                    id=new_id(),
                    account_id=membership.account_id,
                    membership_id=membership.id,
                    property_id=property_id,
                )
            )

        record_change(
            session,
            entity_type="membership",
            entity_id=membership.id,
            # `audit_log.action` is `String(10)`, and every existing value fits it
            # ("create", "update", "rename", "replace"). "scope_changed" does not — caught by
            # the test, as a DataError on the audit insert rather than on the scope write.
            action="rescope",
            changes={"property_ids": [str(p) for p in requested]},
        )
        session.flush()


def property_scopes_by_membership(
    session: Session, account_id: uuid.UUID
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Every staff whitelist in the account, keyed by membership.

    One query for the whole page rather than one per staff row: the members list renders a
    checkbox per property per staff member, and resolving each row's scope separately would put
    a query inside the render loop.

    Absent keys are meaningful. A staff member with no scope rows does not appear here at all,
    and the template's `.get(id, [])` renders that as every box unchecked — which is what zero
    scope rows means (D3), rather than something that failed to load.
    """
    rows = session.execute(
        select(
            MembershipPropertyScope.membership_id,
            MembershipPropertyScope.property_id,
        ).where(MembershipPropertyScope.account_id == account_id)
    ).all()

    scopes: dict[uuid.UUID, set[uuid.UUID]] = {}
    for membership_id, property_id in rows:
        scopes.setdefault(membership_id, set()).add(property_id)
    return scopes
