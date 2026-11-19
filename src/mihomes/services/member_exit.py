"""Leave a household, or delete your own profile — the non-owner half of account deletion.

Slice 4 of the 2026-09-23 plan (built 2026-11-09). The owner's tools act on the whole household
(deactivate, delete); these act on one person:

- **Leave household** (admin or staff): their membership is revoked through the same
  `membership_service.offboard` an owner uses to remove them, so the household keeps their work
  and every consequence of removal follows (sign-in refused, Telegram link ignored, calendar
  events withdrawn). On top of that their staff record stops pointing at their login, and their
  sessions are detached from the household rather than left to fail on the next request.
- **Delete my profile** (anyone who owns no active household): leave every household, then the
  login is scrubbed (`privacy.deletion.scrub_user`): no name, email, password or Google id, every
  session signed out. An owner must transfer or delete their household first; a household with
  no owner could never be managed or deleted again (A22).

Memberships across accounts are read with Core queries under `bind_user_guc`, as sign-in does:
the ORM's tenant filter would show only the current account.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class ExitRefused(RuntimeError):
    """Nothing was changed; the message says why, for the person who asked."""


def _active_memberships(session: Session, user_id) -> list:
    from mihomes.models.membership import Membership
    from mihomes.tenancy.connection import bind_user_guc

    bind_user_guc(session, user_id)
    m = Membership.__table__
    return session.execute(
        sa.select(m.c.id, m.c.account_id, m.c.role)
        .where(m.c.user_id == user_id, m.c.status == "active")
    ).all()


def _leave(session: Session, membership_id, account_id, user_id) -> None:
    """Revoke one membership under its own account's context; unlink staff; detach sessions."""
    from mihomes.models.membership import Membership
    from mihomes.models.session import Session as LoginSession
    from mihomes.models.staff import Staff
    from mihomes.services import membership_service
    from mihomes.tenancy import account_context
    from mihomes.tenancy.connection import bind_account_guc

    with account_context(account_id):
        bind_account_guc(session, account_id)
        membership = session.get(Membership, membership_id)
        if membership is None or membership.status != "active":
            return
        try:
            membership_service.offboard(session, membership)
        except membership_service.MembershipError as exc:
            raise ExitRefused(str(exc)) from exc
        # Their employment record stays the household's; it just stops being their login.
        session.execute(sa.update(Staff.__table__).where(
            Staff.__table__.c.account_id == account_id,
            Staff.__table__.c.user_id == user_id,
        ).values(user_id=None))
    sessions = LoginSession.__table__
    session.execute(sa.update(sessions).where(
        sessions.c.user_id == user_id, sessions.c.current_account_id == account_id,
    ).values(current_account_id=None))
    session.flush()


def leave_household(session: Session, *, user_id, account_id, membership_id, role: str) -> None:
    """The signed-in member leaves the household they are in. Owners cannot: they would leave it
    with no owner, so they transfer ownership or delete it instead."""
    if role == "owner":
        raise ExitRefused("You own this household. Transfer ownership to another member, or "
                          "delete the household, before leaving it.")
    _leave(session, membership_id, account_id, user_id)
    logger.info("member left: user=%s account=%s", user_id, account_id)


def delete_profile(session: Session, *, user_id) -> int:
    """Leave every household and scrub the login. Returns how many households were left."""
    from mihomes.services.privacy.deletion import scrub_user

    memberships = _active_memberships(session, user_id)
    if any(m.role == "owner" for m in memberships):
        raise ExitRefused("You own a household. Transfer ownership or delete that household "
                          "first, then delete your profile.")
    for m in memberships:
        _leave(session, m.id, m.account_id, user_id)
    scrub_user(session, user_id)
    logger.info("profile deleted: user=%s households_left=%s", user_id, len(memberships))
    return len(memberships)
