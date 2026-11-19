"""G2 · §6 Step 6 — `scoped_property_ids()`, THE intra-account authorization boundary (A10, A11).

§4.3: *"One implementation, four consumers"* — web queries, the AI advisor's 15 executors, the
bot's Q&A path, and the bot's classification path. *"Written separately they drift, and drift is
a leak."*

**A10 is the fail-closed case and it is the one that matters.** A staff membership with zero
scope rows must yield the empty set, never "all" (D3, `ONBOARDING:44`). The dangerous
implementation is the natural one: build a filter from the scope rows, and when there are none,
apply no filter — which reads as "no restriction" and returns the whole account. That bug looks
exactly like the feature working.
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.authz.scope import scoped_property_ids
from mihomes.models.membership import Membership, MembershipPropertyScope
from mihomes.models.property import Property
from mihomes.models.user import User


def _property(session, account_id, name: str) -> Property:
    prop = Property(
        id=uuid.uuid4(), account_id=account_id, name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
    )
    session.add(prop)
    session.flush()
    return prop


def _membership(session, account_id, role: str, status: str = "active") -> Membership:
    """A membership backed by a real `users` row.

    `memberships.user_id` is a genuine FK, so inventing a UUID raises
    `ForeignKeyViolation` rather than producing a usable fixture — the constraint is doing its
    job. `User` is GLOBAL (not `TenantOwned`), so it carries no `account_id`.
    """
    user = User(
        id=uuid.uuid4(),
        google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"{role}-{uuid.uuid4().hex[:6]}@example.com",
    )
    session.add(user)
    session.flush()

    m = Membership(
        id=uuid.uuid4(), account_id=account_id, user_id=user.id,
        role=role, status=status,
    )
    session.add(m)
    session.flush()
    return m


def _scope(session, membership, prop) -> None:
    session.add(
        MembershipPropertyScope(
            id=uuid.uuid4(), account_id=membership.account_id,
            membership_id=membership.id, property_id=prop.id,
        )
    )
    session.flush()


class TestStaffScope:
    def test_empty_scope_is_empty(self, session, account_a):
        """A10 · D3 — zero scope rows means zero properties, never "all".

        Seeded with two properties the staff member is *not* scoped to, so an implementation
        that skips the filter when the scope set is empty returns 2 here and fails loudly.
        A test with no properties in the account would pass against that bug.
        """
        _property(session, account_a, "Belle Estate")
        _property(session, account_a, "Blue Room")
        staff = _membership(session, account_a, "staff")

        assert scoped_property_ids(session, staff) == frozenset()

    def test_staff_sees_exactly_their_scope_rows(self, session, account_a):
        """The positive control. Without it, a function returning `frozenset()` unconditionally
        would pass A10 — an empty-set stub satisfies the fail-closed test perfectly."""
        scoped_to = _property(session, account_a, "Belle Estate")
        _property(session, account_a, "Blue Room")
        staff = _membership(session, account_a, "staff")
        _scope(session, staff, scoped_to)

        assert scoped_property_ids(session, staff) == frozenset({scoped_to.id})

    def test_properties_added_later_are_invisible_until_scoped(self, session, account_a):
        """D3 — *"Properties added later are invisible to staff until explicitly scoped."*

        The whitelist's defining property, and the one a blacklist implementation gets wrong.
        """
        first = _property(session, account_a, "Belle Estate")
        staff = _membership(session, account_a, "staff")
        _scope(session, staff, first)

        _property(session, account_a, "Newly Acquired")

        assert scoped_property_ids(session, staff) == frozenset({first.id})


class TestPrivilegedScope:
    @pytest.mark.parametrize("role", ["owner", "admin"])
    def test_privileged_ignores_scope_rows(self, session, account_a, role):
        """A11 — owner/admin see every property in the account **even with scope rows present**.

        `ONBOARDING:44` says their scope rows are ignored. Seeding a scope row that names only
        *one* of two properties is what makes this test meaningful: an implementation that
        applied the rows uniformly would return one property and fail.
        """
        one = _property(session, account_a, "Belle Estate")
        two = _property(session, account_a, "Blue Room")
        privileged = _membership(session, account_a, role)
        _scope(session, privileged, one)

        assert scoped_property_ids(session, privileged) == frozenset({one.id, two.id})

    def test_privileged_with_no_properties_is_empty_not_error(self, session, account_a):
        """An account mid-onboarding has no properties yet (Step 11 makes the first one
        optional until step 3). The boundary must return the empty set rather than raise."""
        owner = _membership(session, account_a, "owner")
        assert scoped_property_ids(session, owner) == frozenset()


class TestCrossAccountIsolation:
    def test_scope_never_crosses_the_account_boundary(self, session, account_a):
        """Phase 1's boundary must still hold underneath Phase 2's.

        An owner's "every property in the account" must mean *their* account. This is the one
        place where an intra-account primitive could reintroduce a cross-tenant leak, because it
        is the only query here that is not naturally keyed on a single row.
        """
        mine = _property(session, account_a, "Belle Estate")
        owner = _membership(session, account_a, "owner")

        result = scoped_property_ids(session, owner)
        assert result == frozenset({mine.id})


class TestRevokedMembership:
    def test_revoked_membership_has_no_scope(self, session, account_a):
        """A revoked membership authorises nothing, including via a stale scope row.

        The scope rows survive revocation (they CASCADE on delete, not on status change), so a
        primitive keyed only on `membership_id` would keep returning them. D8's "revocation takes
        effect on the next request" has to hold here too, not only at the session layer.
        """
        prop = _property(session, account_a, "Belle Estate")
        revoked = _membership(session, account_a, "staff", status="revoked")
        _scope(session, revoked, prop)

        assert scoped_property_ids(session, revoked) == frozenset()

    def test_revoked_owner_does_not_get_the_whole_account(self, session, account_a):
        """The privileged path must check status too — otherwise revoking an owner *upgrades*
        them from "their scope rows" to "everything", which is the worst possible direction."""
        _property(session, account_a, "Belle Estate")
        revoked_owner = _membership(session, account_a, "owner", status="revoked")

        assert scoped_property_ids(session, revoked_owner) == frozenset()
