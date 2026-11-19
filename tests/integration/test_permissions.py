"""G3 · §6 Step 2 — `require_permission`, §9.4's five ordered steps (A6, A7, A8, A9).

**The route-class rule is the substance of this step.** §9.4 leaves undefined what happens when a
grant is `SCOPED` but `target_property` is `None`, and the obvious reading — "None → deny" — is
wrong: `GET /tasks` has no single target, so every collection route would 403 for staff and Step
7's query-layer filtering would be unreachable code (N5). Item routes require a target;
collection routes are authorized by `scoped_property_ids()` constraining the query.

**A8 is about not revealing existence.** A cross-account target yields 404, never 403 — a 403
confirms the row exists, which is exactly what D9 forbids. The distinction is invisible in a UI
and obvious to anyone enumerating ids.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from mihomes.authz.permissions import require_permission
from mihomes.models.membership import Membership, MembershipPropertyScope
from mihomes.models.property import Property
from mihomes.models.user import User
from mihomes.web.deps import RequestPrincipal

# --------------------------------------------------------------------------------------
# Fixtures — the §9 manifest's owner_a / admin_a / staff_a / staff_a_unscoped
# --------------------------------------------------------------------------------------


def _user(session) -> User:
    user = User(
        id=uuid.uuid4(), google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
    )
    session.add(user)
    session.flush()
    return user


def _principal(session, account_id, role: str, scoped_to=()) -> RequestPrincipal:
    """A membership plus the `RequestPrincipal` a live request would carry."""
    user = _user(session)
    membership = Membership(
        id=uuid.uuid4(), account_id=account_id, user_id=user.id,
        role=role, status="active",
    )
    session.add(membership)
    session.flush()
    for prop in scoped_to:
        session.add(
            MembershipPropertyScope(
                id=uuid.uuid4(), account_id=account_id,
                membership_id=membership.id, property_id=prop.id,
            )
        )
    session.flush()
    return RequestPrincipal(
        user_id=user.id, account_id=account_id,
        membership_id=membership.id, role=role,
    )


def _property(session, account_id, name: str) -> Property:
    prop = Property(
        id=uuid.uuid4(), account_id=account_id, name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
    )
    session.add(prop)
    session.flush()
    return prop


@pytest.fixture
def belle(session, account_a):
    return _property(session, account_a, "Belle Estate")


@pytest.fixture
def blue(session, account_a):
    return _property(session, account_a, "Blue Room")


# --------------------------------------------------------------------------------------


class TestRouteClasses:
    def test_item_route_requires_target(self, session, account_a, belle):
        """A6 — a `SCOPED` **item** route with `target_property=None` denies.

        `task.manage` is `SCOPED` for staff and `Access.ITEM`. With no target there is nothing to
        check the scope against, so the only safe answer is refusal — the permissive alternative
        would authorize every task in the account.
        """
        staff = _principal(session, account_a, "staff", scoped_to=[belle])

        with pytest.raises(HTTPException) as excinfo:
            require_permission(session, staff, "task.manage", target_property=None)
        assert excinfo.value.status_code == 403

    def test_collection_route_filters(self, session, account_a, belle):
        """A7 — a `SCOPED` **collection** route with no target must **not** 403 (N5).

        `ai.use` is `SCOPED`/`COLLECTION`. Returning normally is what makes Step 7's query-layer
        filtering reachable; a deny here would 403 every list page for staff.
        """
        staff = _principal(session, account_a, "staff", scoped_to=[belle])
        require_permission(session, staff, "ai.use", target_property=None)

    def test_item_route_allows_in_scope_target(self, session, account_a, belle):
        """The positive control. Without it, a `require_permission` that denied everything would
        satisfy A6 and A8 perfectly."""
        staff = _principal(session, account_a, "staff", scoped_to=[belle])
        require_permission(session, staff, "task.manage", target_property=belle)

    def test_item_route_denies_out_of_scope_target(self, session, account_a, belle, blue):
        """The whitelist's teeth: a property in the same account but outside the staff member's
        scope is refused. This is the intra-account boundary Phase 2 exists to defend."""
        staff = _principal(session, account_a, "staff", scoped_to=[belle])

        with pytest.raises(HTTPException) as excinfo:
            require_permission(session, staff, "task.manage", target_property=blue)
        assert excinfo.value.status_code == 404, (
            "an out-of-scope property must not be distinguishable from one that does not "
            "exist (D9)"
        )

    def test_account_route_ignores_target(self, session, account_a):
        """`Access.ACCOUNT` actions have no property target; passing none is normal."""
        owner = _principal(session, account_a, "owner")
        require_permission(session, owner, "member.manage", target_property=None)


class TestRoleDenials:
    def test_staff_denied_account_level_action(self, session, account_a):
        """Row 9 — finances are ✗ for staff, and the denial is a 403 (the action is refused),
        not a 404 (the account plainly exists and they are a member of it)."""
        staff = _principal(session, account_a, "staff")

        with pytest.raises(HTTPException) as excinfo:
            require_permission(session, staff, "finance.view")
        assert excinfo.value.status_code == 403

    def test_admin_denied_owner_only_action(self, session, account_a):
        """Rows 14-16 — transfer, billing, and deletion are the owner's alone."""
        admin = _principal(session, account_a, "admin")
        for action in ("account.transfer", "billing.manage", "account.delete"):
            with pytest.raises(HTTPException) as excinfo:
                require_permission(session, admin, action)
            assert excinfo.value.status_code == 403, action

    def test_owner_allowed_everywhere(self, session, account_a, belle):
        """The owner's grants are `ALLOW` on all 21 keys, so none of them may raise."""
        from mihomes.authz.actions import MATRIX, Access

        owner = _principal(session, account_a, "owner")
        for key, spec in MATRIX.items():
            target = belle if spec.access is Access.ITEM else None
            require_permission(session, owner, key, target_property=target)

    def test_unknown_action_is_refused(self, session, account_a):
        """An action key not in `MATRIX` must fail closed.

        §9.2's footnote — *"an undeclared action is a deploy-time error, not a silent allow"* —
        is the whole design, and a typo'd key reaching a permissive default would be exactly the
        silent allow it forbids.
        """
        owner = _principal(session, account_a, "owner")
        with pytest.raises((HTTPException, KeyError, ValueError)):
            require_permission(session, owner, "task.mange")


class TestCrossAccount:
    def test_cross_account_is_404(self, session, session_b, account_a, account_b):
        """A8 · D9 — a target in another account is 404, never 403.

        Seeded through the `session_b` fixture so the row genuinely exists and genuinely belongs
        to someone else — the case a 403 would confirm.
        """
        foreign = _property(session_b, account_b, "Someone Else's Villa")
        session_b.flush()

        owner = _principal(session, account_a, "owner")
        with pytest.raises(HTTPException) as excinfo:
            require_permission(session, owner, "property.view", target_property=foreign.id)
        assert excinfo.value.status_code == 404, (
            "a cross-account target must not be distinguishable from a nonexistent one (D9)"
        )

    def test_nonexistent_target_is_also_404(self, session, account_a):
        """The other half of D9: a made-up id and a real foreign id must be indistinguishable.

        If only one of the two were 404, the *pair* of responses would still reveal existence.
        """
        owner = _principal(session, account_a, "owner")
        with pytest.raises(HTTPException) as excinfo:
            require_permission(session, owner, "property.view", target_property=uuid.uuid4())
        assert excinfo.value.status_code == 404


class TestRevocation:
    """A9 · D8/N10 — revocation takes effect on the **next request**.

    **This is deliberately a request-level test, and the distinction is the design (C14).**
    `require_permission` trusts `RequestPrincipal.role` rather than re-reading the membership on
    every call, because `_resolve_authenticated` already loaded it fresh **this request** — D8's
    actual requirement (`ONBOARDING:78`: *"the session stores who and which account is current —
    never the role"*). Re-reading per *call* would be stricter than D8 asks and would add a
    database round trip to every authorization check, of which one page render performs many.

    Testing revocation by mutating the row and calling `require_permission` again with the same
    principal would therefore be testing a guarantee the design does not make — and would force
    the per-call query to make it pass. Crossing a real request boundary tests the guarantee that
    was actually promised.
    """

    def test_revocation_immediate(self, unbound_client, account_a, auth_seed):
        from sqlalchemy import text

        from mihomes.auth.sessions import SESSION_COOKIE

        make, connection = unbound_client
        raw, user_id = auth_seed(connection, account_a, role="owner")

        client = make()
        assert client.get("/__probe__/context", cookies={SESSION_COOKIE: raw}).status_code == 200

        connection.execute(
            text("UPDATE memberships SET status = 'revoked' WHERE user_id = :uid"),
            {"uid": user_id},
        )

        assert client.get(
            "/__probe__/context", cookies={SESSION_COOKIE: raw}
        ).status_code != 200, (
            "revoking a membership must deny on the next request, with no restart and no "
            "session expiry (D8/N10)"
        )
