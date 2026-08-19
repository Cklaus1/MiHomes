"""Account switching — SPEC-003 §6 Step 13, D11 (A24).

D11 carries the current account in a **session field** rather than a path prefix, and records the
known limitation: *"one browser = one current account, so two families cannot be open side by
side (workaround: two browser profiles)."* It is explicitly **reversible** — the revisit trigger
is the first real customer who is staff on two accounts — and *"neither choice affects isolation:
`account_id` scoping plus RLS do that regardless."*

**Hidden entirely for single-account users**, not disabled or greyed out: *"a homeowner who will
never see a second account gets no added clutter."* That is A24, and "absent" is the assertion —
a disabled control is still a control, and still raises the question of what it would do.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.account import Account
from mihomes.models.membership import Membership
from mihomes.models.user import User

__all__ = ["available_accounts", "remember_last_used", "should_show_switcher", "switch_account"]


def available_accounts(session: Session, user_id: uuid.UUID) -> list[Account]:
    """Accounts this user is an **active** member of, by name.

    Read with a Core-style join on `memberships` and `accounts`, both of which the caller may not
    have tenant context for: the whole point of switching is to reach an account that is *not* the
    current one. A `TenantOwned` ORM read of `Membership` would be filtered to the account being
    switched away from, and would return exactly one row — the one the user is trying to leave.
    """
    rows = session.execute(
        select(Account)
        .join(Membership, Membership.account_id == Account.id)
        .where(
            Membership.user_id == user_id,
            Membership.status == "active",
        )
        .order_by(Account.name)
    ).scalars()
    return list(rows)


def should_show_switcher(session: Session, user_id: uuid.UUID) -> bool:
    """A24 — the control is **absent** below two accounts.

    Not "disabled": a greyed-out switcher still occupies space and still invites the question of
    what it would do. §6 Step 13's wording is *"hidden entirely for single-account users"*, and
    the overwhelming majority of this product's users will only ever have one.
    """
    return len(available_accounts(session, user_id)) > 1


def remember_last_used(session: Session, user_id: uuid.UUID, account_id: uuid.UUID) -> None:
    """Persist the account to open at on a **new** session (D11).

    `sessions.current_account_id` covers the current browser; this covers the next one.
    """
    user = session.get(User, user_id)
    if user is not None:
        user.last_used_account_id = account_id
        session.flush()


def switch_account(
    session: Session, session_id: uuid.UUID, user_id: uuid.UUID, account_id: uuid.UUID
) -> bool:
    """Point the session at another account. Returns False if the user may not have it.

    **The membership check lives in `set_current_account`, deliberately, and is not duplicated
    here.** The account id arrives from a form the user controls, so it is checked server-side
    where the write happens — a check here as well would be a second place to get it right, and
    the one that ran first would be the one that mattered.

    `remember_last_used` only runs on success, so a rejected switch cannot poison the default a
    future session opens at.
    """
    from mihomes.auth.sessions import set_current_account

    if not set_current_account(session, session_id, account_id):
        return False

    remember_last_used(session, user_id, account_id)
    return True
