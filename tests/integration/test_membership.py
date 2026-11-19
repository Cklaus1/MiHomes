"""G14 · §6 Step 14 — role change, owner transfer, offboarding (A22, A23).

**Against `memberships` and its partial unique index, never `accounts.owner_user_id`** — B2 says
that column does not exist, and `ONBOARDING` references it three times, so the test that matters
most here is the one asserting the index is what enforces the invariant.

A22 is *"the last owner cannot be removed **or demoted**"* — two verbs, and demotion is the one
an implementation forgets, because it does not look like a removal.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from mihomes.models.membership import Membership
from mihomes.models.user import User
from mihomes.services.membership_service import (
    MembershipError,
    change_role,
    offboard,
    transfer_ownership,
)


def _member(session, account_id, role, status="active") -> Membership:
    user = User(
        id=uuid.uuid4(),
        google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"{role}-{uuid.uuid4().hex[:6]}@example.com",
    )
    session.add(user)
    session.flush()
    membership = Membership(
        id=uuid.uuid4(), account_id=account_id, user_id=user.id,
        role=role, status=status,
    )
    session.add(membership)
    session.flush()
    return membership


def _active_owners(session, account_id) -> int:
    return session.execute(
        text(
            "SELECT count(*) FROM memberships WHERE account_id = :a "
            "AND role = 'owner' AND status = 'active'"
        ),
        {"a": account_id},
    ).scalar_one()


class TestLastOwnerProtected:
    def test_last_owner_protected(self, session, account_a):
        """A22, first verb — the only owner cannot be **removed**.

        An account with no owner is unreachable by anyone: nothing could invite, transfer, or
        delete it, and there is no support path to recover it.
        """
        owner = _member(session, account_a, "owner")

        with pytest.raises(MembershipError, match="only owner cannot be removed"):
            offboard(session, owner)

        assert _active_owners(session, account_a) == 1

    def test_last_owner_cannot_be_demoted(self, session, account_a):
        """A22, second verb — refused for **every** actor, and R1 is why.

        Working out *which* rule does the refusing was worth doing, because the answer is not the
        last-owner check:

        - the owner demoting **themselves** → R1, "nobody may change their own role";
        - an **admin** demoting them → R1, "an admin may not change the owner's role";
        - another **owner** demoting them → impossible, since two active owners cannot exist
          (SPEC-002 D4's partial unique index), so the target is not the *last* owner.

        So the last owner cannot be demoted by anyone, and R1 closes it before the last-owner
        check is ever consulted. That check remains in `change_role` as defence in depth — it
        becomes the operative guard the moment R1 is relaxed, which `ONBOARDING` §11 Q2's
        "granular staff capabilities" would do. Asserting *refusal* rather than a specific
        message is deliberate: pinning R1's wording here would make this test fail if the
        guarantee moved from one correct rule to another.
        """
        owner = _member(session, account_a, "owner")
        admin = _member(session, account_a, "admin")

        for actor in (owner, admin):
            with pytest.raises(MembershipError):
                change_role(session, actor=actor, target=owner, new_role="admin")

        assert owner.role == "owner"
        assert _active_owners(session, account_a) == 1

    def test_the_last_owner_check_is_reachable_on_its_own(self, session, account_a):
        """The defence-in-depth guard, exercised directly.

        Since R1 shadows it through `change_role`, the only way to know it works is to call the
        path that reaches it — `offboard`. Without this, the check could be broken and nothing
        would notice until R1 changed.
        """
        from mihomes.services.membership_service import _is_last_active_owner

        owner = _member(session, account_a, "owner")
        assert _is_last_active_owner(session, owner) is True

        second = _member(session, account_a, "admin")
        transfer_ownership(session, owner, second)
        assert _is_last_active_owner(session, owner) is False, "an ex-owner is not the last one"
        assert _is_last_active_owner(session, second) is True

    def test_an_owner_can_be_removed_when_another_exists(self, session, account_a):
        """The positive control.

        Without it, a guard that refused to offboard *any* owner would satisfy A22 perfectly and
        make co-ownership permanent.
        """
        first = _member(session, account_a, "owner")
        second = _member(session, account_a, "admin")
        transfer_ownership(session, first, second)

        # `first` is now an admin and `second` the owner; removing the ex-owner must work.
        offboard(session, first)
        assert first.status == "revoked"
        assert _active_owners(session, account_a) == 1

    def test_a_revoked_owner_does_not_count_as_the_last_one(self, session, account_a):
        """The predicate reads `status`, not just `role`.

        SPEC-002's index has the same clause for the same reason: *"a partial index without the
        status clause would let a revoked owner block appointing a new one."*
        """
        _revoked = _member(session, account_a, "owner", status="revoked")
        active = _member(session, account_a, "owner")

        # Exactly one *active* owner, so `active` is still protected...
        with pytest.raises(MembershipError):
            offboard(session, active)
        # ...and the revoked row did not inflate the count.
        assert _active_owners(session, account_a) == 1


class TestTransferInvariant:
    def test_transfer_invariant(self, session, account_a):
        """A23 — transfer leaves **exactly one** active owner, via `memberships`."""
        owner = _member(session, account_a, "owner")
        admin = _member(session, account_a, "admin")

        transfer_ownership(session, owner, admin)

        assert admin.role == "owner"
        assert _active_owners(session, account_a) == 1

    def test_outgoing_owner_becomes_admin_not_revoked(self, session, account_a):
        """Handing over ownership is not the same act as leaving.

        Conflating them would silently remove someone's access as a side effect of a handover —
        the sort of thing discovered a week later when they cannot sign in.
        """
        owner = _member(session, account_a, "owner")
        admin = _member(session, account_a, "admin")

        transfer_ownership(session, owner, admin)

        assert owner.role == "admin"
        assert owner.status == "active"

    def test_the_partial_unique_index_is_what_enforces_this(self, session, account_a):
        """**The invariant lives in the database, not in this service** (SPEC-002 D4, B2).

        Asserted by trying to create a second active owner behind the service's back. If this
        insert succeeds, every guarantee above rests on application code remembering to check —
        and B2's whole point is that ownership *is* this index, not an `owner_user_id` column.
        """
        import sqlalchemy.exc

        _member(session, account_a, "owner")
        user = User(
            id=uuid.uuid4(), google_sub=f"sub-{uuid.uuid4().hex[:12]}",
            email=f"second-{uuid.uuid4().hex[:6]}@example.com",
        )
        session.add(user)
        session.flush()

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            session.execute(
                text(
                    "INSERT INTO memberships (id, account_id, user_id, role, status, "
                    "created_at) VALUES (:id, :a, :u, 'owner', 'active', now())"
                ),
                {"id": uuid.uuid4(), "a": account_a, "u": user.id},
            )
        session.rollback()

    def test_there_is_no_owner_user_id_column(self):
        """B2 — `accounts.owner_user_id` does not exist, and must not be reintroduced.

        `ONBOARDING` references it at :35, :43 and :220, so a reader following the source rather
        than the spec would add it back. Ownership is the partial unique index on `memberships`.
        """
        from mihomes.models.account import Account

        assert "owner_user_id" not in Account.__table__.columns

    def test_cannot_transfer_to_a_revoked_member(self, session, account_a):
        """Otherwise a handover to someone offboarded leaves the account ownerless in practice
        while the index reports one owner."""
        owner = _member(session, account_a, "owner")
        gone = _member(session, account_a, "admin", status="revoked")

        with pytest.raises(MembershipError, match="revoked member"):
            transfer_ownership(session, owner, gone)

    def test_cannot_transfer_across_accounts(self, session, session_b, account_a, account_b):
        """Nonsense rather than a leak — but refusing loudly beats writing two rows that
        disagree about which account they belong to."""
        owner = _member(session, account_a, "owner")
        foreign = _member(session_b, account_b, "admin")

        with pytest.raises(MembershipError, match="same account"):
            transfer_ownership(session, owner, foreign)

    def test_only_the_active_owner_can_transfer(self, session, account_a):
        """Row 14 is the owner's alone (`account.transfer` is DENY for admin and staff)."""
        _owner = _member(session, account_a, "owner")
        admin = _member(session, account_a, "admin")
        staff = _member(session, account_a, "staff")

        with pytest.raises(MembershipError, match="only the active owner"):
            transfer_ownership(session, admin, staff)


class TestRoleChangeRules:
    def test_r1_is_enforced_not_reimplemented(self, session, account_a):
        """R1 is *called* from `authz.actions`, not restated here.

        The matrix tests assert R1's behaviour; if the service reimplemented it, those tests
        would pass while the rule that actually runs drifted.
        """
        owner = _member(session, account_a, "owner")
        admin = _member(session, account_a, "admin")
        other = _member(session, account_a, "staff")

        # An admin may not change the owner's role.
        with pytest.raises(MembershipError, match="R1"):
            change_role(session, actor=admin, target=owner, new_role="admin")

        # Nor their own.
        with pytest.raises(MembershipError, match="R1"):
            change_role(session, actor=admin, target=admin, new_role="staff")

        # But may change someone else's.
        change_role(session, actor=admin, target=other, new_role="admin")
        assert other.role == "admin"
        assert owner.role == "owner"

    def test_owner_cannot_be_assigned(self, session, account_a):
        """D2 — the second route to ownership, closed.

        Without this the partial unique index would reject whichever attempt lost, surfacing as a
        database error rather than as a policy decision the user can act on.
        """
        owner = _member(session, account_a, "owner")
        admin = _member(session, account_a, "admin")

        with pytest.raises(MembershipError, match="only by transfer"):
            change_role(session, actor=owner, target=admin, new_role="owner")


class TestOffboardingKeepsTheWork:
    def test_content_stays_with_account(self, session, account_a):
        """`ONBOARDING:225` — tasks, notes, issues and uploads *"stay with the account"*.

        Soft revocation is what makes that true. Deleting the membership row would either orphan
        the work or cascade it away with whoever happened to file it — a housekeeper leaving must
        not take three years of maintenance history with her.
        """
        from mihomes.models.property import Property
        from mihomes.models.task import Task

        owner = _member(session, account_a, "owner")
        leaver = _member(session, account_a, "staff")

        prop = Property(
            id=uuid.uuid4(), account_id=account_a, name="Belle",
            slug=f"belle-{uuid.uuid4().hex[:6]}",
        )
        session.add(prop)
        session.flush()
        task = Task(
            id=uuid.uuid4(), title="Filed by the leaver",
            slug=f"t-{uuid.uuid4().hex[:8]}", property_id=prop.id,
        )
        session.add(task)
        session.flush()

        offboard(session, leaver)

        assert leaver.status == "revoked"
        assert session.get(Task, task.id) is not None, "the account must keep the work"
        assert owner.status == "active"

    def test_offboarding_is_idempotent(self, session, account_a):
        """Revoking twice is not an error — the button gets double-clicked."""
        _owner = _member(session, account_a, "owner")
        leaver = _member(session, account_a, "staff")

        offboard(session, leaver)
        offboard(session, leaver)
        assert leaver.status == "revoked"
