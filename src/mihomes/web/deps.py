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
from mihomes.db import get_session
from mihomes.models.membership import Membership
from mihomes.tenancy import account_context

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
        # Signed in, no account chosen yet: the picker state. G13 turns this into
        # a redirect to the account picker; until then it is a distinct status so
        # the two cases are never conflated.
        raise HTTPException(status_code=403, detail="No account selected")

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
