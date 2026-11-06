"""Deactivate and reactivate a household (Slice 2 of the 2026-09-23 plan; built 2026-11-06).

Decisions (user, 2026-09-23): the **owner** deactivates, and it takes **the whole household**;
everyone else sees "deactivated by the owner". A deactivated account is erased 90 days after
deactivation unless reactivated (the purge job's 90-day rule).

What "deactivated" switches off, and where:
- **Signing in to it:** `set_current_account` refuses the binding; onboarding shows the
  deactivated page instead of looping between `/` and `/onboarding/`.
- **Current sessions:** detached from the account (`current_account_id` → NULL) rather than
  deleted, so a member of another estate stays signed in and lands there.
- **The Telegram bot:** a deactivated account's links and group bindings resolve to nothing, so
  its messages are neither logged nor answered.
- **Scheduled work:** the account sweeps skip it (mail, chat notifications, alerts, calendar).
  The purge job still sees it — that is how the 90-day rule runs.
- **Billing:** a live subscription is cancelled immediately (no refund for the rest of the
  period), and the account comes back on Free. If the cancel fails, deactivation is refused:
  switching a household off while it keeps being charged is the one outcome to avoid.

Reads of `accounts` here are Core reads: it is a global table with no tenant RLS, and the gateway
callers run before any account is bound.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Deactivated this long, an account is erased (the purge job's 90-day rule).
INACTIVE_DAYS_BEFORE_ERASURE = 90


class LifecycleRefused(RuntimeError):
    """The change was not made; the message says why, for the person who asked."""


def _accounts():
    from mihomes.models.account import Account

    return Account.__table__


def is_active(session: Session, account_id) -> bool:
    """False for a deactivated (or purged) account. Unknown ids are not active either."""
    if account_id is None:
        return False
    accounts = _accounts()
    row = session.execute(
        sa.select(accounts.c.deactivated_at).where(accounts.c.id == account_id)
    ).first()
    return row is not None and row.deactivated_at is None


def was_purged(session: Session, account_id) -> bool:
    """True once the account's data has been erased: it can never be reactivated."""
    from mihomes.models.account_deletion import AccountDeletionRequest
    from mihomes.tenancy.connection import bind_account_guc

    bind_account_guc(session, account_id)  # the request is a tenant row
    table = AccountDeletionRequest.__table__
    return session.execute(
        sa.select(table.c.id).where(table.c.account_id == account_id,
                                    table.c.purged_at.is_not(None)).limit(1)
    ).first() is not None


def is_owner(session: Session, account_id, user_id) -> bool:
    """Is `user_id` the active owner of `account_id`? Read across accounts under the user GUC,
    because the caller may have no account bound (the deactivated page)."""
    from mihomes.models.membership import Membership
    from mihomes.tenancy.connection import bind_user_guc

    bind_user_guc(session, user_id)
    m = Membership.__table__
    return session.execute(
        sa.select(m.c.id).where(m.c.user_id == user_id, m.c.account_id == account_id,
                                m.c.role == "owner", m.c.status == "active").limit(1)
    ).first() is not None


def deactivate(session: Session, account, *, now: datetime | None = None, billing=None) -> int:
    """Switch the household off. Returns how many sessions were detached."""
    from mihomes.models.session import Session as LoginSession
    from mihomes.services.billing.cancel import has_live_subscription

    now = now or datetime.now(UTC)
    if account.deactivated_at is not None:
        return 0
    if has_live_subscription(account):
        try:
            if billing is None:
                from mihomes.services.billing.provider import get_billing_provider

                billing = get_billing_provider("stripe")
            billing.cancel(subscription_id=account.stripe_subscription_id, at_period_end=False)
        except Exception as exc:  # noqa: BLE001 — any failure means "not switched off"
            logger.warning("deactivate refused: account=%s billing cancel failed: %s", account.id, exc)
            raise LifecycleRefused(
                "The subscription couldn't be cancelled, so the household was not deactivated. "
                "Try again in a few minutes."
            ) from exc
        account.subscription_status = "canceled"
    account.plan = "free"
    account.trial_ends_at = None
    account.cancel_at_period_end = False
    account.deactivated_at = now

    sessions = LoginSession.__table__
    detached = session.execute(
        sa.update(sessions).where(sessions.c.current_account_id == account.id)
        .values(current_account_id=None)
    ).rowcount or 0
    session.flush()
    logger.info("account deactivated: account=%s sessions_detached=%s", account.id, detached)
    return detached


def reactivate(session: Session, account_id, user_id) -> None:
    """Switch the household back on — the owner only, never after a purge."""
    from mihomes.models.account import Account

    if not is_owner(session, account_id, user_id):
        raise LifecycleRefused("Only the household's owner can reactivate it.")
    if was_purged(session, account_id):
        raise LifecycleRefused("This household was deleted and can't be reactivated.")
    account = session.get(Account, account_id)
    if account is None or account.deactivated_at is None:
        return
    account.deactivated_at = None
    session.flush()
    logger.info("account reactivated: account=%s by user=%s", account_id, user_id)


#: `requested_by_user_id` for a deletion the 90-day rule starts. Nobody asked: the column is
#: NOT NULL and deliberately not a foreign key, and the nil UUID says "the system" without
#: pointing at a real person.
SYSTEM_REQUESTER = uuid.UUID(int=0)
