"""Nightly clean-up of data nothing needs any more (2026-11-10).

Until this existed, nothing deleted old Messenger-log rows, sent mail, waitlist sign-ups or
expired sign-in rows: they accumulated forever, which the Privacy Policy cannot describe and no
household asked for. The periods were decided by the founder on 2026-11-10:

| Data | Kept |
|---|---|
| Messenger log (`chat_messages`, the Conversations page) | `retention.whatsapp_years`, default 1 year |
| Sent / failed mail (`email_outbox`) and delivery results (`email_deliveries`) | 90 days |
| Waitlist sign-ups sent a confirmation email and never confirmed | 30 days |
| Waitlist IP address and browser details | 30 days (confirmed sign-ups keep the rest) |
| Sign-in sessions, password-reset links, Telegram link codes | until they expire |

Mail still **waiting** to send is never touched, nor is the unsubscribe list (`email_suppressions`),
which must outlive everything so an unsubscribed address is never mailed again.

Two halves, because the tables are of two kinds. `prune_account` runs under one account's context
(the tables are `TenantOwned` and RLS-scoped — on the app role an unbound delete removes nothing
and reports success). `prune_global` runs with no account bound, on the global tables that sign-in
and the public website read before any account exists.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

__all__ = [
    "EMAIL_RETENTION",
    "MESSENGER_DEFAULT_YEARS",
    "WAITLIST_TRACKING",
    "WAITLIST_UNCONFIRMED",
    "messenger_retention",
    "prune_account",
    "prune_global",
]

MESSENGER_DEFAULT_YEARS = 1
EMAIL_RETENTION = timedelta(days=90)
WAITLIST_UNCONFIRMED = timedelta(days=30)
WAITLIST_TRACKING = timedelta(days=30)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def messenger_retention(session: Session) -> timedelta:
    """How long this account keeps its Messenger log — the operator setting, or 1 year.

    `retention.whatsapp_years` is operator-only (`config_service.USER_SETTING_KEYS` excludes
    `retention.*`). A value that is not a whole number of years of at least one falls back to the
    default rather than being honoured: `0` must not quietly mean "delete every message tonight".
    """
    from mihomes.services.config_service import get_config

    raw = get_config(session, "retention.whatsapp_years", str(MESSENGER_DEFAULT_YEARS))
    try:
        years = int(str(raw).strip())
    except (TypeError, ValueError):
        years = MESSENGER_DEFAULT_YEARS
    if years < 1:
        logger.warning("retention.whatsapp_years=%r is not a positive whole number; using %d",
                       raw, MESSENGER_DEFAULT_YEARS)
        years = MESSENGER_DEFAULT_YEARS
    return timedelta(days=365 * years)


def prune_account(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Delete the bound account's expired data. Returns rows removed per kind; caller commits."""
    from mihomes.models.chat_message import ChatMessage
    from mihomes.models.email_delivery import EmailDelivery
    from mihomes.models.email_outbox import EmailOutbox
    from mihomes.models.gateway_link_token import GatewayLinkToken

    now = _now(now)
    mail_cutoff = now - EMAIL_RETENTION

    def run(statement) -> int:
        return session.execute(statement).rowcount or 0

    return {
        "messenger": run(delete(ChatMessage).where(
            ChatMessage.sent_at < now - messenger_retention(session))),
        # Finished rows only: a row with neither timestamp is still queued or retrying.
        "outbox": run(delete(EmailOutbox).where(or_(
            EmailOutbox.sent_at < mail_cutoff, EmailOutbox.failed_at < mail_cutoff))),
        "deliveries": run(delete(EmailDelivery).where(EmailDelivery.sent_at < mail_cutoff)),
        "link_codes": run(delete(GatewayLinkToken).where(GatewayLinkToken.expires_at < now)),
    }


def prune_global(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Delete expired sign-in rows and stale waitlist data. Returns counts; caller commits."""
    from mihomes.models.password_reset_token import PasswordResetToken
    from mihomes.models.session import Session as SignInSession
    from mihomes.models.waitlist import Waitlist

    now = _now(now)

    def run(statement) -> int:
        return session.execute(statement).rowcount or 0

    return {
        "sessions": run(delete(SignInSession).where(SignInSession.expires_at < now)),
        "reset_links": run(delete(PasswordResetToken).where(PasswordResetToken.expires_at < now)),
        # Only rows that were *asked* to confirm. Waitlist mode (`web/routes/waitlist.py`)
        # sends no confirmation while mail is not live; without this condition the nightly run
        # would delete every one of those sign-ups after 30 days.
        "waitlist_unconfirmed": run(delete(Waitlist).where(
            Waitlist.confirmed_at.is_(None),
            Waitlist.confirm_sent_at.is_not(None),
            Waitlist.created_at < now - WAITLIST_UNCONFIRMED,
        )),
        "waitlist_tracking_cleared": run(
            update(Waitlist)
            .where(
                Waitlist.created_at < now - WAITLIST_TRACKING,
                or_(Waitlist.signup_ip.is_not(None), Waitlist.user_agent.is_not(None)),
            )
            .values(signup_ip=None, user_agent=None)
        ),
    }
