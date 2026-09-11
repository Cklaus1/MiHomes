"""The settings Members section — the refusals, which are the part worth pinning.

Every test here is a **refusal**, and that is deliberate. The happy paths (a role changes, a
scope is written) are the easy half and would pass against a version of this code with every
guard removed. What distinguishes correct code from dangerous code in member management is what
it turns away: R1's two prohibitions, A22's last-owner rule, D3's fail-closed scope, and the
self-revoke lockout that no existing rule covered.

`create_invite` returning its token is also pinned here. Nothing else asserts it, the existing
route at `team.py:114` discards it, and a refactor that redirected instead would silently restore
the defect this feature exists to fix.
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.models.membership import Membership, MembershipPropertyScope
from mihomes.models.property import Property
from mihomes.models.user import User
from mihomes.services import membership_service
from mihomes.services.invite_service import create_invite
from mihomes.services.membership_service import (
    MembershipError,
    list_members,
    offboard,
    property_scopes_by_membership,
    set_property_scope,
)


def _user(session, email: str | None = None, name: str | None = None) -> User:
    user = User(
        id=uuid.uuid4(),
        google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=email or f"u-{uuid.uuid4().hex[:6]}@example.com",
        name=name,
    )
    session.add(user)
    session.flush()
    return user


def _member(session, account_id, role: str, *, name: str | None = None) -> Membership:
    user = _user(session, name=name)
    membership = Membership(
        id=uuid.uuid4(), account_id=account_id, user_id=user.id,
        role=role, status="active",
    )
    session.add(membership)
    session.flush()
    return membership


@pytest.fixture
def belle(session, account_a) -> Property:
    prop = Property(
        id=uuid.uuid4(), account_id=account_a, name="Belle Estate",
        slug=f"belle-{uuid.uuid4().hex[:6]}",
    )
    session.add(prop)
    session.flush()
    return prop


@pytest.fixture
def cove(session, account_a) -> Property:
    prop = Property(
        id=uuid.uuid4(), account_id=account_a, name="The Cove",
        slug=f"cove-{uuid.uuid4().hex[:6]}",
    )
    session.add(prop)
    session.flush()
    return prop


class TestRoleChangeRefusals:
    """R1 — row 13's *"(not owner's, not own)"*, which no grant cell can express."""

    def test_admin_cannot_change_the_owners_role(self, session, account_a):
        """Without this, an admin demotes the owner and takes the estate.

        This is the escalation row 13 exists to prevent, and it is the reason R1 is a function
        rather than a matrix cell: the answer depends on who the target is relative to the actor.
        """
        owner = _member(session, account_a, "owner")
        admin = _member(session, account_a, "admin")

        with pytest.raises(MembershipError, match="may not change"):
            membership_service.change_role(session, admin, owner, "staff")

        session.refresh(owner)
        assert owner.role == "owner", "the owner must survive an admin's attempt to demote them"

    def test_nobody_changes_their_own_role(self, session, account_a):
        """Self-promotion would be a second, unaudited route to ownership.

        Asserted for an admin because that is the case with something to gain; the owner is
        refused by the same clause.
        """
        admin = _member(session, account_a, "admin")

        with pytest.raises(MembershipError, match="may not change"):
            membership_service.change_role(session, admin, admin, "staff")

    def test_owner_cannot_be_assigned_by_role_change(self, session, account_a):
        """D2 — ownership moves only by transfer.

        The partial unique index would reject this anyway, but as an `IntegrityError` surfacing
        as a 500. Refusing it as policy is what makes it a message rather than a crash.
        """
        owner = _member(session, account_a, "owner")
        staff = _member(session, account_a, "staff")

        with pytest.raises(MembershipError, match="transfer"):
            membership_service.change_role(session, owner, staff, "owner")


class TestOffboardRefusals:
    def test_last_active_owner_cannot_be_removed(self, session, account_a):
        """A22 — an account with no owner is unreachable by anyone.

        Nothing could invite, transfer, or delete it afterwards, and there is no support path to
        recover it, so this refusal is the difference between a mistake and an unrecoverable one.
        """
        owner = _member(session, account_a, "owner")

        with pytest.raises(MembershipError, match="only owner"):
            offboard(session, owner)

        session.refresh(owner)
        assert owner.status == "active"

    def test_offboard_is_a_soft_revoke(self, session, account_a):
        """`ONBOARDING:225` — the account keeps the work.

        A housekeeper leaving must not take three years of maintenance history with her, which is
        why the row survives with `status='revoked'` rather than being deleted.
        """
        _member(session, account_a, "owner")
        staff = _member(session, account_a, "staff")

        offboard(session, staff)

        session.refresh(staff)
        assert staff.status == "revoked"
        assert session.get(Membership, staff.id) is not None, "the row must survive"


class TestPropertyScope:
    def test_staff_cannot_be_left_with_zero_properties(self, session, account_a, belle):
        """D3 — zero scope rows means zero properties visible, and that fails closed.

        A staff member who can sign in and see nothing cannot distinguish that from the product
        being broken. The fail-closed direction means the fix cannot be "grant all", so the only
        safe answer is to refuse the write and say so.
        """
        staff = _member(session, account_a, "staff")
        set_property_scope(session, staff, [belle.id])

        with pytest.raises(MembershipError, match="at least one property"):
            set_property_scope(session, staff, [])

        # The refusal must not have cleared what was already there.
        scopes = property_scopes_by_membership(session, account_a)
        assert scopes[staff.id] == {belle.id}

    def test_scope_is_refused_for_owner_and_admin(self, session, account_a, belle):
        """`scoped_property_ids` ignores their rows (`ONBOARDING:44`), so writing them is a lie.

        Rows that look like they govern access and do not are worse than none: the next reader
        believes they are the whitelist and narrows someone who is in fact unrestricted.
        """
        admin = _member(session, account_a, "admin")

        with pytest.raises(MembershipError, match="every property"):
            set_property_scope(session, admin, [belle.id])

    def test_scope_rejects_a_property_from_another_estate(
        self, session, session_b, account_a, account_b
    ):
        """A property id arrives from a form, so it cannot be trusted to belong here."""
        staff = _member(session, account_a, "staff")

        foreign = Property(
            id=uuid.uuid4(), account_id=account_b, name="Not Yours",
            slug=f"foreign-{uuid.uuid4().hex[:6]}",
        )
        session_b.add(foreign)
        session_b.flush()

        with pytest.raises(MembershipError, match="not part of this estate"):
            set_property_scope(session, staff, [foreign.id])

        assert session.query(MembershipPropertyScope).filter_by(
            membership_id=staff.id
        ).count() == 0

    def test_scope_replaces_rather_than_accumulates(self, session, account_a, belle, cove):
        """A whitelist that only ever grew would make removing access impossible."""
        staff = _member(session, account_a, "staff")

        set_property_scope(session, staff, [belle.id, cove.id])
        assert property_scopes_by_membership(session, account_a)[staff.id] == {
            belle.id, cove.id
        }

        set_property_scope(session, staff, [cove.id])
        assert property_scopes_by_membership(session, account_a)[staff.id] == {cove.id}

    def test_the_change_is_audited(self, session, account_a, belle):
        """The audit row is what proves the tenant binding, not decoration.

        `audit_log` is tenant-owned while the write is driven from a route, so the account has
        to be bound with **both** the ContextVar and the GUC — `services/account.py:83-85`
        records measuring the alternative: the change committed and the audit insert was
        refused under RLS, leaving the estate altered and no trail of it. Asserting the row
        exists is what makes that failure loud if the pairing is ever dropped.
        """
        from mihomes.models.audit_log import AuditLog

        staff = _member(session, account_a, "staff")
        set_property_scope(session, staff, [belle.id])

        entry = (
            session.query(AuditLog)
            .filter_by(entity_type="membership", entity_id=staff.id, action="rescope")
            .one()
        )
        assert entry.changes["property_ids"] == [str(belle.id)]


class TestMemberListing:
    def test_revoked_members_are_absent(self, session, account_a):
        """`offboard` is a soft revoke, so a query keyed only on the account would list them."""
        _member(session, account_a, "owner")
        staff = _member(session, account_a, "staff")
        offboard(session, staff)

        listed = {m.membership_id for m in list_members(session, account_a)}
        assert staff.id not in listed

    def test_ordered_owner_then_admin_then_staff(self, session, account_a):
        """An org chart rather than an insertion log.

        Built deliberately out of order — staff first, owner last — so a passing result cannot be
        insertion order wearing a sort's clothes.
        """
        _member(session, account_a, "staff", name="Zoe")
        _member(session, account_a, "admin", name="Yusuf")
        _member(session, account_a, "owner", name="Xena")

        assert [m.role for m in list_members(session, account_a)] == [
            "owner", "admin", "staff",
        ]

    def test_display_name_falls_back_to_email(self, session, account_a):
        """A member who has never set a name still has to render as somebody."""
        member = _member(session, account_a, "admin", name=None)
        view = next(
            m for m in list_members(session, account_a) if m.membership_id == member.id
        )

        assert view.name is None
        assert view.display_name == view.email


class TestInviteToken:
    def test_create_invite_returns_the_raw_token(self, session, account_a, belle):
        """**The defect this feature exists to fix**, pinned so it cannot come back.

        Only the hash is persisted, so the plaintext exists for exactly one moment in the
        process. `team.py:114` discards it and no invite email template exists, which makes an
        invitation there a row, a consumed seat, and a link nobody can retrieve. The settings
        route renders it instead; this asserts there is something to render.
        """
        invite, raw = create_invite(
            session, account_a, None, "new@example.com", "staff", [belle.id]
        )

        assert raw, "the caller must receive a token, or the invitation cannot be delivered"
        assert raw not in (invite.token_hash or ""), "the raw token must not be recoverable"
