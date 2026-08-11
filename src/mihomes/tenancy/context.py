"""Tenant context — the ContextVars every scoped query reads (SPEC-002 §4.4, §5).

Two variables and a contextmanager. Deliberately small: this module holds the
*state*, and G8's session listeners (`do_orm_execute`, `before_flush`,
`after_begin`) are what act on it. Built here, ahead of §6's Step 8, because the
test fixtures need to bind an account and nothing here depends on RLS or the scoped
session existing.

**`require_account()` never returns None.** A nullable accessor invites
`if account: ...` checks that silently skip scoping — which is the failure mode this
whole phase exists to prevent, so it raises instead. Failing closed is the point:
no context means no query, not an unscoped query.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

__all__ = [
    "account_context",
    "current_account",
    "current_user",
    "require_account",
    "require_user",
]

# No default. `.get()` on an unset ContextVar raises LookupError, which is exactly
# the fail-closed behaviour §4.4 wants — a default of None would make every caller
# responsible for remembering to check.
current_account: ContextVar[uuid.UUID] = ContextVar("current_account")
current_user: ContextVar[uuid.UUID] = ContextVar("current_user")


@contextmanager
def account_context(
    account_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> Iterator[None]:
    """Bind the tenant for a block. Resets ContextVars on exit, exception-safe.

    Uses the token/reset protocol rather than assigning back a saved value: that is
    what makes nesting correct, and it restores "unset" as distinct from "set to the
    previous value" — the difference matters because unset is what fails closed.
    """
    account_token = current_account.set(account_id)
    user_token = current_user.set(user_id) if user_id is not None else None
    try:
        yield
    finally:
        # Reset in reverse order, and in a finally so an exception inside the block
        # cannot leak tenant context to whatever runs next. On a pooled connection
        # that leak would be a cross-tenant read.
        if user_token is not None:
            current_user.reset(user_token)
        current_account.reset(account_token)


def require_account() -> uuid.UUID:
    """Current account, or raise LookupError.

    Never returns None — see the module docstring. Callers that genuinely need to
    ask "is there context?" should catch LookupError explicitly, which makes the
    unscoped path visible in the code rather than implied by a falsy check.
    """
    return current_account.get()


def require_user() -> uuid.UUID:
    """Current user, or raise LookupError.

    Needed by the `membership_self` bootstrap policy (§4.3), which is keyed on
    `app.current_user` rather than the account — it is how the account picker works
    before an account has been chosen.
    """
    return current_user.get()
