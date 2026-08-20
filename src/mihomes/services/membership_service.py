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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mihomes.models.membership import Membership

__all__ = [
    "MembershipError",
    "change_role",
    "offboard",
    "transfer_ownership",
]


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
