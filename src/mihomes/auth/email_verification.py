"""Email verification — mint, verify, redeem (2026-11-13).

**An unverified password user never gets a session.** The gate sits where sessions are created
(signup, login, this module's redeem), not in a middleware that every route would have to pass
through. That is why there is no "unverified but signed in" state to reason about anywhere else
in the app: no half-access, no banner, no list of routes an unverified user may still reach.

The token is the reset token's pattern (`password_reset.py`): only the sha256 hash is stored,
it is single-use, and it expires. The module docstring there explains why the hash is not a
slow KDF.

## Squatting, and why re-signup does not overwrite

Anyone can sign up with someone else's address and never confirm it. The owner then meets
"an account with that email already exists". **Their way back is password reset**: the link
goes to their inbox, `redeem_reset_token` replaces the squatter's password, and redeeming it
also stamps `email_verified_at` (`mark_verified`), because it proves the same thing.

Letting a second signup *overwrite* an unverified row looks friendlier and is an account
takeover. If an attacker re-signs a few seconds after the real owner, the owner clicks the
link in their inbox and activates the **attacker's** password. Google sign-in is the other
way back: `oidc.upsert_user` claims an unverified row and clears its password.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from mihomes.models.email_verification_token import EmailVerificationToken
from mihomes.models.user import User

__all__ = [
    "VERIFY_TTL",
    "find_unverified_password_user",
    "hash_token",
    "issue_verification_token",
    "mark_verified",
    "redeem_verification_token",
    "verify_verification_token",
]

#: See `models/email_verification_token.py` for why 24 hours.
VERIFY_TTL = timedelta(hours=24)


def hash_token(raw: str) -> str:
    """`sha256(raw)` as hex, identical to `password_reset.hash_token`."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def issue_verification_token(db: DbSession, user_id: uuid.UUID) -> str:
    """Mint a token, store only its hash, and return the raw value **once**. **Does not commit.**

    Earlier live tokens stay valid. Someone who pressed "resend" twice and clicks the first
    email should not find a dead link. All of them die together on redemption.
    """
    raw = secrets.token_urlsafe(32)
    db.add(
        EmailVerificationToken(
            user_id=user_id,
            token_hash=hash_token(raw),
            expires_at=_now() + VERIFY_TTL,
        )
    )
    db.flush()
    return raw


def verify_verification_token(db: DbSession, raw: str) -> EmailVerificationToken | None:
    """The row for a **live** token (right hash, unused, unexpired), or `None`.

    Wrong, used and expired all collapse to `None`, as in `verify_reset_token`.
    """
    if not raw:
        return None
    row = db.execute(
        select(EmailVerificationToken).where(EmailVerificationToken.token_hash == hash_token(raw))
    ).scalar_one_or_none()
    if row is None or row.used_at is not None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= _now():
        return None
    return row


def mark_verified(db: DbSession, user: User) -> None:
    """Stamp `email_verified_at` if unset, and burn every live verification token for the user.

    Shared by redemption and password reset. Each one proves the person controls the mailbox,
    and once that is proven no outstanding link should still sign anyone in. **Does not commit.**
    """
    now = _now()
    if user.email_verified_at is None:
        user.email_verified_at = now
    db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    db.flush()


def redeem_verification_token(db: DbSession, raw: str) -> User | None:
    """Verify the user behind a live token and burn it. `None` when the token is not live.

    **Does not commit**; the caller signs the person in within the same transaction.
    """
    row = verify_verification_token(db, raw)
    if row is None:
        return None
    user = db.get(User, row.user_id)
    if user is None:  # pragma: no cover - FK makes this unreachable
        return None
    mark_verified(db, user)
    return user


def find_unverified_password_user(db: DbSession, email: str) -> User | None:
    """The unverified password user on this address, or `None`. Resend's lookup only."""
    from mihomes.auth.password_identity import find_password_user

    user = find_password_user(db, email)
    if user is None or user.email_verified_at is not None:
        return None
    return user
