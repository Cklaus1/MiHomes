"""`require_permission` — SPEC-003 §9.4's five ordered steps (A6, A7, A8, A9).

**Signature note (pre-flight C14).** §5 writes
`require_permission(user, current_account, action, target_property=None)`. That argument list
cannot reach `scoped_property_ids(session, membership)` — it has neither a session nor a
membership. What §5 actually specifies is the *behaviour*: five ordered steps, 404 on a
cross-account target (D9), 403 on a role denial, and the item/collection route-class rule. The
argument list is not the rule, so `RequestPrincipal` (which carries user, account, membership,
and role) replaces its first two arguments.

**The order of the checks is load-bearing, not incidental.** Target resolution runs *before* the
role lookup, so a cross-account target is 404 even for a role that would otherwise be denied 403.
Reversing them would leak existence through the status code: an attacker probing ids would learn
"this row exists but you may not touch it" (403) versus "no such row" (404), which is exactly
what D9 forbids.

**The role is not re-read here (C14).** `_resolve_authenticated` loads it fresh from the database
once per request, which is D8's actual requirement (`ONBOARDING:78`). Re-reading per *call* would
be stricter than D8 asks and would add a round trip to every check, of which one page render
performs many. **This is correct only because the principal came from a live request
resolution** — a call site that builds a `RequestPrincipal` from a cached value or a queue
message breaks D8 silently.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.authz.actions import MATRIX, Access, Grant
from mihomes.authz.audit import audit_deny
from mihomes.authz.scope import scoped_property_ids
from mihomes.models.membership import Membership
from mihomes.models.property import Property

if TYPE_CHECKING:  # pragma: no cover
    from mihomes.web.deps import RequestPrincipal

__all__ = ["require_action_gate", "require_permission"]


def require_action_gate(session: Session, principal: RequestPrincipal, action: str) -> Grant:
    """§9.4 steps 0–3 only: the action must be declared, and the role must not be `DENY`.

    Returns the resolved `Grant` so the caller knows whether scope filtering still applies.
    **This is deliberately not the whole of `require_permission`.**

    A `SCOPED` grant cannot be settled here, because settling it needs a *target*, and a target
    only exists once the route has resolved its slug to a row. §9.4 step 4 says scoped access is
    *"filtered to scoped homes at the query layer, not post-hoc"* — so the query layer is where
    `SCOPED` is answered, and this function's job is to reject the role outright when the matrix
    says `DENY` and otherwise get out of the way.

    Splitting it this way is what lets **one** app-level dependency enforce all 142 declarations
    instead of 142 hand-edits. Calling `require_permission` there instead would 403 every staff
    request to an item route, because `target_property` is still `None` at dependency time (A6) —
    which is exactly the unreachable-code failure N5 warns about.
    """
    spec = MATRIX.get(action)
    if spec is None:
        raise _audited_denial(
            principal, action, 403, f"Unknown action {action!r}", None
        )

    grant = {
        "owner": spec.owner,
        "admin": spec.admin,
        "staff": spec.staff,
    }.get(principal.role, Grant.DENY)  # an unrecognised role fails closed

    if grant is Grant.DENY:
        raise _audited_denial(
            principal, action, 403, f"{principal.role} may not {action}", None
        )

    return grant


def _audited_denial(
    principal: RequestPrincipal, action: str, status: int, reason: str, target_id
) -> HTTPException:
    """Audit a refusal and build the exception, so no denial path can forget the audit (A33)."""
    audit_deny(
        account_id=principal.account_id,
        actor=str(principal.user_id),
        action=action,
        role=principal.role,
        reason=reason,
        target_id=target_id,
    )
    return HTTPException(status_code=status, detail=reason)


def _target_property_id(target: Property | uuid.UUID | None) -> uuid.UUID | None:
    if target is None:
        return None
    if isinstance(target, uuid.UUID):
        return target
    return target.id


def require_permission(
    session: Session,
    principal: RequestPrincipal,
    action: str,
    target_property: Property | uuid.UUID | None = None,
    *,
    audit_session_factory=None,
) -> None:
    """Authorize `action` for `principal`, or raise.

    Raises `HTTPException(404)` on a cross-account or out-of-scope target (D9 — never reveal
    existence) and `HTTPException(403)` on a role denial. Returns `None` when permitted.

    Route classes (§6 Step 2):

    - `Access.ITEM` — `target_property` is **required** when the grant is `SCOPED`; `None` denies.
    - `Access.COLLECTION` — no target; the *query* is constrained by `scoped_property_ids()`.
      Returning normally here is what makes Step 7 reachable (N5).
    - `Access.ACCOUNT` — no property target exists.

    **Every refusal is audited** (A33, §9.4's closing paragraph), in its own transaction — see
    `authz/audit.py` for why the request's session cannot carry it.
    """
    target_id = _target_property_id(target_property)

    def _deny(status: int, reason: str) -> HTTPException:
        """Audit the refusal, then build the exception for the caller to raise.

        Returning rather than raising keeps every denial a single `raise _deny(...)` at the call
        site, so a path that forgets to audit is visibly different from one that does.
        """
        audit_deny(
            account_id=principal.account_id,
            actor=str(principal.user_id),
            action=action,
            role=principal.role,
            reason=reason,
            target_id=target_id,
            session_factory=audit_session_factory,
        )
        return HTTPException(status_code=status, detail=reason)

    # ---- Step 0: the action must be declared. §9.2's footnote: "an undeclared action is a
    # deploy-time error, not a silent allow." A typo'd key must not reach a permissive default.
    spec = MATRIX.get(action)
    if spec is None:
        raise _deny(403, f"Unknown action {action!r}")

    # ---- Step 1: resolve the target. Before the role check, so existence is never revealed by
    # the difference between 403 and 404 (D9).
    if target_id is not None:
        in_account = session.execute(
            select(Property.id).where(
                Property.id == target_id,
                Property.account_id == principal.account_id,
            )
        ).scalar_one_or_none()
        if in_account is None:
            # Covers both "belongs to another account" and "does not exist". The two must be
            # indistinguishable: if only one were 404, the *pair* of responses would still leak.
            raise _deny(404, "Not found")

    # ---- Step 2: the role. Loaded fresh this request by the dependency; see the module
    # docstring for why it is not re-read here.
    grant = {
        "owner": spec.owner,
        "admin": spec.admin,
        "staff": spec.staff,
    }.get(principal.role, Grant.DENY)  # an unrecognised role fails closed

    # ---- Step 3: apply the grant.
    if grant is Grant.DENY:
        raise _deny(403, f"{principal.role} may not {action}")

    if grant is Grant.ALLOW:
        return

    # ---- Step 4: SCOPED — the route class decides what "scoped" means here.
    if spec.access is Access.COLLECTION:
        # No target to check. The query layer applies `scoped_property_ids()`; denying here
        # would 403 every list page for staff and make Step 7 unreachable code (N5).
        return

    if spec.access is Access.ACCOUNT:
        # A SCOPED grant on an account-class action has nothing to scope by, so it could only be
        # resolved by ignoring the scope — i.e. by silently granting. The matrix forbids this
        # combination; reaching it means MATRIX was edited past its own test.
        raise _deny(403, f"{action} is account-class and cannot carry a scoped grant")

    # Access.ITEM
    if target_id is None:
        # Nothing to check the scope against. The permissive alternative would authorize every
        # record in the account (A6).
        raise _deny(403, f"{action} requires a target property")

    membership = session.get(Membership, principal.membership_id)
    if membership is None:
        raise _deny(403, "No active membership")

    if target_id not in scoped_property_ids(session, membership):
        # Same status as a cross-account target: an out-of-scope property must not be
        # distinguishable from one that does not exist (D9, and §6 Step 7's explicit wording —
        # "yields 404, not an empty list").
        raise _deny(404, "Not found")
