"""FastAPI dependencies shared across all routes.

**`require_authenticated` is what binds the tenant to a web request** (SPEC-003
pre-flight C12). Before it, `account_context()` was entered only by the CLI
(`cli/__init__.py:81`) and by the test fixtures — so the web application had
sign-in and a session store but no per-request binding, and every tenant query
in a deployed request would raise `LookupError` from `require_account()`. The
web suite passed regardless, because `conftest.web_client_factory` binds the
account around the whole test. SPEC-003's `require_permission(user,
current_account, ...)` has no source for its first two arguments without this.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.auth.sessions import SESSION_COOKIE, lookup_session
from mihomes.authz.actions import Access, Grant
from mihomes.authz.declare import declared_action
from mihomes.authz.permissions import require_action_gate
from mihomes.authz.query_scope import install_property_scope_listener
from mihomes.authz.redact import redact_context
from mihomes.authz.scope import authz_context, current_role, scoped_property_ids
from mihomes.db import get_session
from mihomes.models.membership import Membership
from mihomes.tenancy import account_context, current_user

TEMPLATES_DIR = Path(__file__).parent / "templates"

#: The detail on the one 403 that is **recoverable**, and the reason it is a named constant.
#:
#: Two unrelated conditions raise 403 in this app and they need opposite treatment:
#:
#:   *this*                    signed in, but no account is selected yet — the answer is to
#:                             finish onboarding, so a browser should be *sent* there
#:   `authz.permissions`       "your role may not do this" — a real denial, and redirecting it
#:                             anywhere would be nonsense (a staff member who opened
#:                             `/settings` does not need the onboarding wizard)
#:
#: `app.py`'s 403 handler tells them apart by matching this exact string, so it lives here
#: rather than being written out at each `raise`. A literal repeated in three files is how the
#: handler and the raise sites drift apart, and the failure mode of that drift is silent: the
#: redirect simply stops happening, and the dead end returns looking like the original bug.
NO_ACCOUNT_SELECTED = "No account selected"


class RedactingTemplates(Jinja2Templates):
    """Jinja2Templates that redacts the context before rendering (SPEC-003 §6 Step 8).

    **This is redaction at the serialization boundary, not in the templates — N3.** The
    distinction matters: a Jinja filter would leave the AI path unprotected, because it renders no
    templates, which is F3's exact shape. The context dict is the last place a row exists as an
    object, so redacting it here covers every page written so far *and* every page written later,
    without a single template knowing about roles.

    Every route in the app already imports this module's `templates` singleton, so wrapping it is
    one edit rather than 142 — the same reasoning as G7's single enforcement dependency.
    """

    def TemplateResponse(self, *args, **kwargs):
        role = current_role.get()

        # Starlette's signature moved: the modern form is
        # `TemplateResponse(request, name, context=None, ...)`, the legacy one
        # `TemplateResponse(name, context, ...)`. Handle the context wherever it actually is
        # rather than assuming a position — guessing wrong would silently skip redaction on
        # whichever form the codebase does not use today, and it uses both shapes across 24
        # router files.
        if "context" in kwargs:
            kwargs["context"] = redact_context(kwargs["context"], role)
        else:
            args = list(args)
            for index, value in enumerate(args):
                if isinstance(value, dict):
                    args[index] = redact_context(value, role)
                    break
            args = tuple(args)

        return super().TemplateResponse(*args, **kwargs)


templates = RedactingTemplates(directory=str(TEMPLATES_DIR))


def _time_label(t) -> str:
    """Format a time/datetime as '1pm', '1:30pm', etc. for calendar badges."""
    if t is None:
        return ""
    from datetime import datetime as _dt
    if isinstance(t, _dt):
        t = t.time()
    hour = int(t.strftime("%I"))  # 12-hour, strip leading zero via int()
    ampm = t.strftime("%p").lower()
    if t.minute == 0:
        return f"{hour}{ampm}"
    return f"{hour}:{t.minute:02d}{ampm}"


templates.env.filters["time_label"] = _time_label


def get_db() -> Generator[Session, None, None]:
    with get_session() as session:
        yield session


# The Core table, so the membership lookup carries no ORM mappers and therefore
# does not re-enter the G8 tenant filter while the tenant is still being decided.
# Same reasoning as `auth/sessions.py`'s `_MEMBERSHIPS`, and the same reason
# `skip_tenant` is not used: N9 forbids putting the codebase's `sudo` on the hot
# path of every request.
_MEMBERSHIPS = Membership.__table__

# Explicit, rather than relying on the import above having a side effect: the query-scope
# listener must be installed before any request runs, and a linter that removes an
# "unused" import would otherwise silently disable staff scoping.
install_property_scope_listener()


@dataclass(frozen=True)
class RequestPrincipal:
    """Who is acting, in which account, as what — resolved fresh every request.

    Carries `membership_id` because SPEC-003's `scoped_property_ids(membership)`
    is keyed on the membership, not the user: a user may hold memberships in
    several accounts with different scopes.

    **The role is deliberately re-read per request and never cached in the
    session row** (D8, N10, `ONBOARDING:78`). Revocation must take effect on the
    next request, which is only true if nothing here is remembered.
    """

    user_id: uuid.UUID
    account_id: uuid.UUID
    membership_id: uuid.UUID
    role: str


def session_user_id(request: Request, db: Session) -> uuid.UUID:
    """The signed-in user, **without requiring an account** (`Access.SESSION`).

    `lookup_session` refuses an expired or revoked session, so this is not a weaker
    authentication check — only a narrower one: it stops before asking *which account*, because
    the routes that use it are the ones deciding that.
    """
    auth = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    if auth is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return auth.user_id


def resolve_principal(request: Request, db: Session) -> RequestPrincipal:
    """Cookie → `RequestPrincipal`, or raise. **Does not bind context** — callers do that.

    Factored out of the dependency so the app-level enforcement gate can call it *conditionally*.
    A sub-dependency cannot be conditional: it runs before the gate can look at whether the route
    is declared, so an undeclared-but-allowlisted route (sign-in, the OIDC callback) would 401
    before reaching its own handler — authentication depending on being authenticated.
    """
    auth = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    if auth is None:
        # One outcome for every failure — no cookie, expired, unknown, or membership revoked.
        # `lookup_session` collapses them deliberately so a caller cannot treat a revoked session
        # as merely account-less.
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not auth.has_account or auth.account_id is None:
        # Signed in, no account chosen yet: the picker state. `app.py`'s 403 handler now turns
        # this into a redirect to `/onboarding/` for a browser, keying on the detail string —
        # so it stays a distinct status, and is no longer a dead end for the user who hits it.
        raise HTTPException(status_code=403, detail=NO_ACCOUNT_SELECTED)

    membership = db.execute(
        select(_MEMBERSHIPS.c.id, _MEMBERSHIPS.c.role).where(
            _MEMBERSHIPS.c.user_id == auth.user_id,
            _MEMBERSHIPS.c.account_id == auth.account_id,
            _MEMBERSHIPS.c.status == "active",
        )
    ).one_or_none()
    if membership is None:
        # `lookup_session` already rejects a revoked membership; this is the narrow race where it
        # is revoked between the two reads. Failing closed costs one refused request.
        raise HTTPException(status_code=401, detail="Not authenticated")

    return RequestPrincipal(
        user_id=auth.user_id,
        account_id=auth.account_id,
        membership_id=membership.id,
        role=membership.role,
    )


async def enforce_declared_action(
    request: Request, db: Session = Depends(get_db)
) -> AsyncIterator[RequestPrincipal | None]:
    """**The single place all 142 route declarations are enforced.**

    Reads the matched route's `@declares(...)` attributes and applies the role gate. One
    dependency instead of 142 hand-edits — and, more importantly, one place that cannot be
    forgotten: Step 4's harness guarantees every route carries a declaration, and this guarantees
    every declaration is consulted. N1's warning ("the edits are hopeful rather than verified")
    is answered by the pair, not by either alone.

    **Undeclared routes are left alone**, which is safe only because `test_no_undeclared_routes`
    has an empty temporary allowlist: the sole undeclared module is `auth`, which must stay
    reachable to unauthenticated callers.

    **`SCOPED` is not settled here** — see `require_action_gate`. Resolving it needs a target,
    which needs the route's slug lookup, so scope is enforced at the query layer (§9.4 step 4).
    """
    route = request.scope.get("route")
    declared = declared_action(route.endpoint) if route is not None else None

    if declared is None:
        yield None
        return

    action, access = declared

    if access is Access.SESSION:
        # Authorised by being signed in, not by a role within an account (G13.5). The account is
        # what these routes are about to establish, accept an invitation into, or change — so
        # resolving one first would 403 every screen that needs to run before it exists.
        #
        # `current_user` is still bound, so audit rows made here name the real actor and the
        # onboarding service can find the person it is onboarding. `current_account` is
        # deliberately left **unset**: `require_account()` then raises rather than silently
        # scoping to whatever was ambient, which is the fail-closed direction.
        user_id = session_user_id(request, db)
        token = current_user.set(user_id)
        try:
            yield None
        finally:
            current_user.reset(token)
        return

    principal = resolve_principal(request, db)
    with account_context(principal.account_id, principal.user_id):
        grant = require_action_gate(db, principal, action)

        # `SCOPED` is answered here, by binding the whitelist the query layer reads (§9.4
        # step 4) — never by refusing the request, which would 403 every list page for staff
        # and make this code unreachable (N5).
        #
        # **`None` for privileged roles, not "all property ids".** Enumerating every property
        # into a set would be equivalent today and wrong tomorrow: a property created during
        # the request would fall outside a snapshot taken at its start, and the failure would
        # look like a caching bug rather than an authorization one.
        scope = None
        if grant is Grant.SCOPED:
            membership = db.get(Membership, principal.membership_id)
            scope = scoped_property_ids(db, membership) if membership else frozenset()

        # Role and scope bind together: a request with a scope but no role would filter rows
        # while redacting nothing, which reads as "redaction is broken" rather than "the role
        # was never set".
        with authz_context(principal.role, scope):
            yield principal


async def _resolve_authenticated(
    request: Request, db: Session = Depends(get_db)
) -> AsyncIterator[RequestPrincipal]:
    """Resolve the session cookie and bind the tenant for the request's duration.

    **This is an `async` generator on purpose, and switching it to `def` would
    silently break it.** FastAPI runs a *sync* dependency's body in a threadpool
    (`contextmanager_in_threadpool`), so a `ContextVar.set()` inside one applies
    to the worker thread's copy of the context and is discarded before the
    endpoint runs. An async dependency's body executes in the request's own task,
    and Starlette then *copies* that context into the threadpool when it calls a
    sync endpoint — so the binding is visible exactly where queries run. The
    end-to-end probe in `tests/integration/test_request_context.py` exists to
    catch a regression here, because the sync version fails only at runtime and
    only on the paths that touch tenant data.
    """
    auth = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    if auth is None:
        # One outcome for every failure — no cookie, expired, unknown, or
        # membership revoked. `lookup_session` collapses them deliberately so a
        # caller cannot treat a revoked session as merely account-less.
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not auth.has_account or auth.account_id is None:
        # Signed in, no account chosen yet: the picker state. Same condition and same constant
        # as `resolve_principal` above, so `app.py`'s 403 handler redirects a browser here too
        # — this path is reached by routes taking `require_authenticated()` directly.
        raise HTTPException(status_code=403, detail=NO_ACCOUNT_SELECTED)

    membership = db.execute(
        select(_MEMBERSHIPS.c.id, _MEMBERSHIPS.c.role).where(
            _MEMBERSHIPS.c.user_id == auth.user_id,
            _MEMBERSHIPS.c.account_id == auth.account_id,
            _MEMBERSHIPS.c.status == "active",
        )
    ).one_or_none()
    if membership is None:
        # `lookup_session` already rejects a revoked membership; this is the
        # narrow race where it is revoked between the two reads. Failing closed
        # costs one refused request and is the safe direction.
        raise HTTPException(status_code=401, detail="Not authenticated")

    with account_context(auth.account_id, auth.user_id):
        yield RequestPrincipal(
            user_id=auth.user_id,
            account_id=auth.account_id,
            membership_id=membership.id,
            role=membership.role,
        )


def require_authenticated() -> Any:
    """`Depends(...)` for an authenticated, account-bound request.

    A factory rather than the callable itself so route signatures read
    `principal: RequestPrincipal = require_authenticated()` — one import, and
    the `Depends` wrapper cannot be forgotten at a call site.
    """
    return Depends(_resolve_authenticated)
