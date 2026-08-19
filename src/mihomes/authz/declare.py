"""Route action declarations — SPEC-003 §6 Step 4/5, F1.

F1: *"146 endpoints need an action declaration, and nothing enforces that they have one."*
§9.2's footnote is the whole design — *"an undeclared action is a deploy-time error, not a silent
allow"* — but it names no mechanism, and *"a missed declaration on a **write** route is an
authorization bypass, not a cosmetic omission."*

**The declaration lives on the endpoint, not in a central registry**, because a registry keyed on
`(method, path)` drifts the moment a path is edited and fails silently when it does — the route
still exists, its registry entry no longer matches, and the harness reports a *missing*
declaration for a route that has one while the renamed route sails through unmapped. An attribute
on the function cannot desynchronise from the function.

**Each endpoint declares two facts, not one** (Step 2's route-class rule): *what* the action is,
and *which route class* it is declared on. The class defaults to the action's own `Access`, which
is right for the overwhelming majority; passing it explicitly is for the cases where one action
legitimately appears on both an item and a collection route.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from mihomes.authz.actions import MATRIX, Access

__all__ = [
    "ACCESS_ATTR",
    "ACTION_ATTR",
    "SESSION_ACTION",
    "declared_action",
    "declares",
    "declares_session",
]

ACTION_ATTR = "__mihomes_action__"
ACCESS_ATTR = "__mihomes_access__"

#: The pseudo-action carried by `Access.SESSION` routes.
#:
#: Deliberately **not** a `MATRIX` key. Adding a 21st key would break A1, which asserts the matrix
#: covers `ONBOARDING` §9.2's rows 1-20 exactly — and it would be a lie besides: these routes are
#: not authorised by a role within an account, which is the only thing the matrix describes.
SESSION_ACTION = "session.self"

F = TypeVar("F", bound=Callable)


def declares(action: str, access: Access | None = None) -> Callable[[F], F]:
    """Declare the action (and route class) an endpoint is authorized by.

    Raises at **import time** on an unknown action key — which is the literal content of §9.2's
    *"deploy-time error, not a silent allow"*. A typo'd key that only failed when the route was
    first exercised would be an authorization gap living in production until someone clicked it.
    """
    if action not in MATRIX:
        raise ValueError(
            f"@declares({action!r}) is not a MATRIX action key — see authz/actions.py. "
            "An undeclared action is a deploy-time error, not a silent allow (§9.2)."
        )

    resolved = access if access is not None else MATRIX[action].access

    def decorator(fn: F) -> F:
        setattr(fn, ACTION_ATTR, action)
        setattr(fn, ACCESS_ATTR, resolved)
        return fn

    return decorator


def declares_session(reason: str) -> Callable[[F], F]:
    """Declare a route as authorised by **being signed in**, not by a role in an account.

    `reason` is required and is not decorative: `Access.SESSION` opts a route out of the
    capability matrix, so every use is a small hole in the thing Step 4 exists to guarantee. A
    one-line justification at the site is what keeps the set reviewable — the same discipline the
    permanent allowlist carries, and for the same reason.

    **This is not "unauthenticated".** The route still requires a signed-in user; what it skips is
    the account resolution and the matrix lookup, because the account is what it is about to
    establish, accept an invitation into, or change.
    """
    if not reason or len(reason) < 20:
        raise ValueError(
            "@declares_session needs a real justification — it opts a route out of the "
            "capability matrix, and an unreviewable exemption is how that guarantee erodes"
        )

    def decorator(fn: F) -> F:
        setattr(fn, ACTION_ATTR, SESSION_ACTION)
        setattr(fn, ACCESS_ATTR, Access.SESSION)
        fn.__mihomes_session_reason__ = reason
        return fn

    return decorator


def declared_action(endpoint) -> tuple[str, Access] | None:
    """`(action, access)` for an endpoint, or `None` if it carries no declaration."""
    action = getattr(endpoint, ACTION_ATTR, None)
    if action is None:
        return None
    if action == SESSION_ACTION:
        # Not a MATRIX key by design — do not look it up.
        return action, Access.SESSION
    return action, getattr(endpoint, ACCESS_ATTR, MATRIX[action].access)
