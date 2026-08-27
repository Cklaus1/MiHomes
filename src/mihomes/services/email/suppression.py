"""Suppression list and unsubscribe tokens (SPEC-005 §5.3, D13).

Two responsibilities that belong together because the token is what authorises a write to the
list: `unsubscribe_token` mints it, `verify_unsubscribe_token` checks it, and `suppress` is the
only writer.

## The token is an HMAC, never a raw address

A bare `/unsubscribe?email=someone@example.com` lets anyone unsubscribe anyone — including a
competitor unsubscribing a customer from their own billing mail. The token is an HMAC over the
address under the app secret, which makes the URL self-authenticating with **no token table**:
nothing to expire, nothing to clean up, and it keeps working after the account that sent the mail
is deleted. Same discipline as SPEC-001 N7's confirmation tokens.

Comparison is `hmac.compare_digest`, not `==`. String equality on a secret leaks its prefix
through timing; it is the kind of thing that is obviously fine until someone measures it.

## Suppression applies to lifecycle mail only (D13/N3)

A receipt for money taken, a deletion confirmation and an export link are not marketing. They must
send regardless of unsubscribe state — suppressing them is not caution, it is withholding a record
the customer is owed. The choke point that enforces this is `EmailService._send`, not here.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mihomes.crypto import EncryptionUnavailable, secret_key
from mihomes.models.email_suppression import SUPPRESSION_REASONS, EmailSuppression

__all__ = [
    "InvalidUnsubscribeToken",
    "unsubscribe_headers",
    "is_suppressed",
    "suppress",
    "unsubscribe_token",
    "verify_unsubscribe_token",
]

logger = logging.getLogger(__name__)


class InvalidUnsubscribeToken(ValueError):
    """The token does not match the address it was presented with."""


def _normalize(address: str) -> str:
    """Lower-cased and stripped.

    The list is keyed by address, and `Someone@Example.com` unsubscribing must suppress
    `someone@example.com` — otherwise the next send re-mails a complainer on a casing
    difference. Applied in one place so the token, the lookup and the insert cannot disagree:
    a token minted over one form and verified against another would reject every real click.
    """
    return address.strip().lower()


def unsubscribe_token(address: str) -> str:
    """An HMAC over the address under `MIHOMES_SECRET_KEY`. Stateless — no token table.

    Raises `EncryptionUnavailable` when no key is configured rather than falling back to an
    unsigned token. A fallback would mean unsubscribe links that anyone can forge, in a
    deployment where nothing looks wrong.
    """
    key = secret_key()
    if key is None:
        raise EncryptionUnavailable(
            "MIHOMES_SECRET_KEY is not set, so unsubscribe tokens cannot be signed. "
            "Generate one with `mihomes config generate-key` and put it in the environment."
        )
    return hmac.new(
        key.encode("utf-8"), _normalize(address).encode("utf-8"), sha256
    ).hexdigest()


def verify_unsubscribe_token(address: str, token: str) -> None:
    """Raise `InvalidUnsubscribeToken` unless `token` is this address's token.

    Returns `None` on success rather than a bool: a bool invites `if verify(...)` at the call
    site, and a call site that forgets the `if` fails open. Raising cannot be ignored silently.
    """
    if not hmac.compare_digest(unsubscribe_token(address), token or ""):
        raise InvalidUnsubscribeToken("unsubscribe token does not match the address")


#: Where the unsubscribe link points. An env var rather than a constant because the value differs
#: per deployment and appears in mail that outlives any one release — a hard-coded host in a sent
#: email cannot be corrected later.
UNSUBSCRIBE_BASE_URL_ENV = "MIHOMES_BASE_URL"
DEFAULT_BASE_URL = "https://app.mihomes.ai"


def unsubscribe_headers(address: str) -> dict[str, str]:
    """RFC 8058's two headers for one recipient.

    **Both, or neither.** `List-Unsubscribe` alone is the older RFC 2369 form, which mailbox
    providers render as a link rather than a one-click button; `List-Unsubscribe-Post` is what
    promotes it. The second without the first is meaningless, and the first without the second
    silently downgrades every unsubscribe to a multi-step flow — which is the deliverability
    cost N10 is about, arriving through a different door.

    Lives here rather than in `EmailService` because the token does: `outbox.drain` needs these
    headers at send time and `service.py` already imports `outbox`, so building them there
    would be a circular import. Measured, not predicted — the first version raised one.
    """
    from urllib.parse import urlencode

    base = os.environ.get(UNSUBSCRIBE_BASE_URL_ENV, DEFAULT_BASE_URL).rstrip("/")
    query = urlencode({"email": address, "token": unsubscribe_token(address)})
    return {
        "List-Unsubscribe": f"<{base}/unsubscribe?{query}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def is_suppressed(session: Session, address: str) -> bool:
    """Is this address on the list?"""
    stmt = select(EmailSuppression.id).where(
        EmailSuppression.address == _normalize(address)
    )
    return session.execute(stmt).first() is not None


def suppress(
    session: Session,
    address: str,
    *,
    reason: str,
    provider_event_id: str | None = None,
    now: datetime | None = None,
) -> EmailSuppression | None:
    """Add an address to the list. Idempotent — a second call is a no-op, not an error.

    **Insert-first, not check-then-insert** (the same discipline as SPEC-004's webhook ledger,
    N4). Bounce and complaint webhooks for one address arrive more than once and concurrently:
    two deliveries both see "not present" and both insert, and one of them raises. The unique
    constraint is the mechanism; the violation is the signal.

    Returns the row it created, or `None` when the address was already suppressed. The caller
    can tell "newly suppressed" from "already was" — which the unsubscribe route uses to avoid
    logging a second unsubscribe as if it were a first.

    The **first** reason wins. A complaint that follows an unsubscribe does not overwrite it:
    both mean "do not send", and the earliest record is the one that explains why the address
    stopped receiving mail.
    """
    if reason not in SUPPRESSION_REASONS:
        raise ValueError(
            f"unknown suppression reason {reason!r}; expected one of {SUPPRESSION_REASONS}"
        )

    row = EmailSuppression(
        address=_normalize(address),
        reason=reason,
        suppressed_at=now or datetime.now(UTC),
        provider_event_id=provider_event_id,
    )
    try:
        # Flush rather than commit: suppression usually happens inside a caller's transaction
        # (a webhook handler, an unsubscribe request) and committing here would commit their
        # work too. The savepoint is what lets us swallow the violation without poisoning the
        # outer transaction — a plain rollback would discard the caller's changes as well.
        #
        # `add` goes INSIDE the savepoint, not before it. Outside, the pending object survives
        # the savepoint's rollback and is retried on the caller's next flush — which raises the
        # same violation again, from somewhere unrelated, with the caller's own work lost.
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        logger.info("address already suppressed: reason=%s", reason)
        return None
    return row
