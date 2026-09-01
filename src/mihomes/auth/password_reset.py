"""Password reset — mint, verify, redeem (SPEC-010 §6 Step 5, A11/A12/A13, D8).

**sha256 here, scrypt next door, and the inversion is deliberate (§0.6).** `passwords.py` uses a
salted, slow KDF because a password is human-chosen and appears in every leaked-credential list.
A reset token is 256 bits of `secrets` output: there is no dictionary to attack, so a slow hash
would buy nothing — and would actively hurt, because verification happens on an *unauthenticated*
endpoint. A scrypt-hashed token turns every request into a deliberate CPU burn, which hands an
attacker a denial-of-service tool.

Salting is impossible here regardless: the lookup finds a row *by* the hash, and a per-row salt
would mean hashing the candidate once per stored token.

So this is `invites`, `sessions`, `waitlist` and `gateway_link_tokens`'s pattern verbatim — the
fifth instance — and `passwords.py` is the one place in the tree that departs from it.

## The three properties, and which is invisible

* **Single-use** (A12) — `used_at` is stamped on redemption. Enforced by checking it is NULL,
  not by deleting the row: a used token that still exists can be told apart from one that never
  existed, which matters when someone reports a link that "didn't work".
* **Expiry** (A12) — one hour. Shorter than the invite's seven days because a reset link is a
  live credential for an *existing* account and lands in an inbox that may itself be the thing
  that was compromised.
* **Every session dies** (A13) — and this is the one the spec flags as most likely to be
  forgotten, because **the feature demos perfectly without it**. Set a new password, sign in, it
  works. What does not happen is the attacker being logged out — and the whole reason someone
  resets a password under duress is to evict whoever else is in there.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from mihomes.auth.password_identity import find_password_user, set_password
from mihomes.auth.sessions import revoke_all_sessions
from mihomes.models.password_reset_token import PasswordResetToken

__all__ = [
    "RESET_TTL",
    "hash_token",
    "issue_reset_token",
    "redeem_reset_token",
    "verify_reset_token",
]

#: One hour. See the module docstring for why this is not the invite's seven days.
RESET_TTL = timedelta(hours=1)


def hash_token(raw: str) -> str:
    """`sha256(raw)` as hex — what the database stores.

    Deliberately identical to `invite_service.hash_token`. Not imported from there: that module
    is about seats and memberships, and importing it here would couple password reset to billing
    concepts for the sake of one line.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_reset_token(db: DbSession, user_id: uuid.UUID) -> str:
    """Mint a token, store only its hash, return the raw value **once**.

    The caller emails it and then cannot recover it — which is the point, and is why a failure
    to send strands the reset rather than merely delaying it.

    **Does not commit.**
    """
    raw = secrets.token_urlsafe(32)
    db.add(
        PasswordResetToken(
            user_id=user_id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(timezone.utc) + RESET_TTL,
        )
    )
    db.flush()
    return raw


def verify_reset_token(db: DbSession, raw: str) -> PasswordResetToken | None:
    """The row for a **live** token: right hash, unused, unexpired. `None` otherwise.

    All three failure modes collapse to `None` on purpose. Telling someone "that link has already
    been used" rather than "that link is invalid" confirms a token existed, and the routes render
    one message for every case anyway — so distinguishing them here would only invite a caller to
    leak the difference later.
    """
    if not raw:
        return None

    row = db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_token(raw)
        )
    ).scalar_one_or_none()

    if row is None:
        return None
    if row.used_at is not None:
        return None

    # `expires_at` is timezone-aware in Postgres but comes back naive from some drivers; compare
    # in UTC either way rather than trusting the round trip.
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        return None

    return row


def redeem_reset_token(db: DbSession, raw: str, new_password: str):
    """Set the new password, mark the token used, and **revoke every session** (A13).

    Returns the `User` on success, `None` when the token is not live. **Does not commit** — the
    caller owns the transaction, so a failure anywhere below leaves no half-applied reset.

    **The revocation is the half that is invisible.** Without it the person resetting their
    password because someone else is in their account has changed the lock and left the intruder
    inside: every existing session cookie keeps working until it expires, which for a 14-day TTL
    is not a security response by any reading.

    Ordering matters. The password is set first, the token stamped second, sessions revoked last
    — all inside one transaction, so a crash cannot leave a used token whose password never
    changed, which would lock the user out with no way back.
    """
    row = verify_reset_token(db, raw)
    if row is None:
        return None

    # Resolve the user directly rather than through a relationship: `users` is GLOBAL and this
    # runs before any account context exists, so a lazy load through a tenant-scoped path would
    # demand the account this flow does not have.
    from mihomes.models.user import User  # local import: avoids a cycle at module load

    user = db.get(User, row.user_id)
    if user is None:  # pragma: no cover - FK makes this unreachable
        return None

    set_password(db, user, new_password)

    row.used_at = datetime.now(timezone.utc)
    db.flush()

    # A13. Every session, not just the current one — the whole point is evicting someone else.
    revoke_all_sessions(db, user.id)
    db.flush()

    return user


def find_user_for_reset(db: DbSession, email: str):
    """The password user for an address, or `None`.

    A thin wrapper over `find_password_user` so the reset route reads as intent rather than
    mechanism — and so the `password_hash IS NOT NULL` restriction is stated once. A Google-only
    account has no password to reset; offering one would be a route to hijacking it.
    """
    return find_password_user(db, email)
