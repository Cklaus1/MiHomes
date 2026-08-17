"""G12 · §6 Step 12 — server-side sessions (A16, A17).

**Only the hash reaches the database.** The raw session id is generated once, returned to the
caller for the cookie, and never stored — the `sessions` table holds `sha256(raw)`. So a database
disclosure (a backup, a log, a read-only leak) yields no usable session: an attacker would have to
invert SHA-256. Same discipline as SPEC-001's waitlist confirm token, and the reason
`Session.session_id_hash` is named that rather than `session_id`.

**No salt and no bcrypt, deliberately.** A session id is 256 bits of `secrets` output, not a
human-chosen password: there is no dictionary to attack, so a slow KDF buys nothing and would add
per-request latency to every authenticated call. Salting would break the lookup, since the point is
to find a row *by* the hash. This is the one place a bare SHA-256 is the right primitive.

**Membership is read with a Core select, not the ORM, and that is a design decision (not a
workaround).** `Membership` is `TenantOwned`, so an ORM query against it invokes the G8 filter — which
demands an account context. But authentication is precisely the path that runs *before* any account
context exists: resolving a session is how the account gets chosen. An ORM read here would be
circular, and reaching for `skip_tenant` would put the codebase's `sudo` on the hot path of every
request (N9 forbids exactly that).

A Core `select(memberships)` carries no mappers, so `state.all_mappers` is empty and the filter
correctly skips it — the same mechanism that lets sign-in read GLOBAL `users`. The tenant boundary is
still enforced, one layer down: RLS's `membership_self` policy (A10, keyed on `app.current_user`) is
what makes this read safe, and it exists for exactly this bootstrap case. Two independent mechanisms,
each applying where the other cannot.

**Revocation is checked on every request, never cached in the session (A17).** The membership is
re-read and re-validated per lookup, so revoking access denies it on the *next* request rather than
whenever the session happens to expire. That is the whole content of A17, and it is why
`lookup_session` returns None for an intact session whose membership was revoked — the session row
survives, but it authorises nothing.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from mihomes.models.membership import Membership
from mihomes.models.session import Session as SessionRow
from mihomes.models.user import User

__all__ = [
    "SESSION_COOKIE",
    "SESSION_TTL",
    "AuthenticatedSession",
    "create_session",
    "hash_session_id",
    "lookup_session",
    "revoke_all_sessions",
    "revoke_session",
    "set_current_account",
]

SESSION_COOKIE = "mihomes_session"
SESSION_TTL = timedelta(days=14)

# The Core table, so membership reads carry no ORM mappers. See the module docstring.
_MEMBERSHIPS = Membership.__table__

# 32 bytes -> 43 URL-safe characters. Guessing one is not a threat model at this size; the reason
# to be explicit is that a *shorter* id would be, and defaults drift.
_SESSION_ID_BYTES = 32


def hash_session_id(raw: str) -> str:
    """`sha256(raw)` as hex — what the database stores.

    A plain digest is correct here; see the module docstring for why a KDF would be wrong.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthenticatedSession:
    """What a valid session authorises, resolved fresh on every request."""

    session_id: uuid.UUID
    user_id: uuid.UUID
    account_id: uuid.UUID | None
    role: str | None
    expires_at: datetime

    @property
    def has_account(self) -> bool:
        """False right after sign-in, before an account is chosen (the picker state)."""
        return self.account_id is not None


def create_session(db: DbSession, user_id: uuid.UUID) -> tuple[str, SessionRow]:
    """Create a session, returning `(raw_id_for_the_cookie, row)`.

    The raw id is returned rather than stored because this is the only moment it exists in the
    process. A caller that wants it later has to read the cookie — which is the point.
    """
    raw = secrets.token_urlsafe(_SESSION_ID_BYTES)
    row = SessionRow(
        session_id_hash=hash_session_id(raw),
        user_id=user_id,
        expires_at=datetime.now(timezone.utc) + SESSION_TTL,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return raw, row


def lookup_session(db: DbSession, raw: str | None) -> AuthenticatedSession | None:
    """Resolve a raw cookie value to what it currently authorises, or None.

    Returns None — never a partially-valid object — for every failure: no cookie, unknown hash,
    expired, user gone, or **membership revoked**. A single "not authenticated" outcome means a
    caller cannot accidentally treat a revoked session as merely account-less.

    **The membership check is the A17 requirement.** It happens here, on every request, rather than
    being decided once at sign-in and cached in the row. An intact session whose membership was
    revoked authorises nothing, immediately.
    """
    if not raw:
        return None

    row = db.execute(
        select(SessionRow).where(SessionRow.session_id_hash == hash_session_id(raw))
    ).scalar_one_or_none()
    if row is None:
        return None

    # Compare in UTC. A naive `expires_at` from a database that returned it without a tzinfo would
    # raise on comparison, so normalise rather than assume.
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        return None

    # The user must still exist. `ondelete="CASCADE"` means a deleted user takes its sessions with
    # it, so this is belt-and-braces against a partially-applied delete — cheap, and the failure it
    # guards is "a deleted user still has a live session".
    user = db.get(User, row.user_id)
    if user is None:
        return None

    role: str | None = None
    account_id: uuid.UUID | None = row.current_account_id
    if account_id is not None:
        membership = db.execute(
            select(_MEMBERSHIPS.c.role, _MEMBERSHIPS.c.status).where(
                _MEMBERSHIPS.c.user_id == row.user_id,
                _MEMBERSHIPS.c.account_id == account_id,
            )
        ).one_or_none()
        # A revoked or missing membership does not merely drop the account — it denies the request.
        # Downgrading to "signed in, no account" would leave a revoked user able to re-pick the
        # account they were removed from.
        if membership is None or membership.status != "active":
            return None
        role = membership.role

    row.last_seen_at = datetime.now(timezone.utc)
    return AuthenticatedSession(
        session_id=row.id,
        user_id=row.user_id,
        account_id=account_id,
        role=role,
        expires_at=expires,
    )


def set_current_account(db: DbSession, session_id: uuid.UUID, account_id: uuid.UUID) -> bool:
    """Bind an account to a session, if the user really is an active member of it.

    Checked here rather than trusted from the request: the account arrives from a form the user
    controls, so without this a user could bind *any* account id and every later request would
    accept it — `lookup_session`'s check would pass because the session says so.
    """
    row = db.get(SessionRow, session_id)
    if row is None:
        return False
    membership = db.execute(
        select(_MEMBERSHIPS.c.status).where(
            _MEMBERSHIPS.c.user_id == row.user_id,
            _MEMBERSHIPS.c.account_id == account_id,
        )
    ).one_or_none()
    if membership is None or membership.status != "active":
        return False
    row.current_account_id = account_id
    db.flush()
    return True


def revoke_session(db: DbSession, raw: str) -> None:
    """Sign out one session. Deletes the row rather than flagging it.

    A `revoked` column would need every lookup to remember to filter on it; a deleted row cannot be
    forgotten about. Session history is not something this table is for.
    """
    db.execute(
        SessionRow.__table__.delete().where(
            SessionRow.session_id_hash == hash_session_id(raw)
        )
    )


def revoke_all_sessions(db: DbSession, user_id: uuid.UUID) -> int:
    """Sign out everywhere. Returns how many sessions were ended.

    The count is returned so the UI can say "signed out of 3 devices" — and so a test can prove the
    other sessions were really ended rather than just the current one.
    """
    result = db.execute(
        SessionRow.__table__.delete().where(SessionRow.user_id == user_id)
    )
    return result.rowcount or 0
