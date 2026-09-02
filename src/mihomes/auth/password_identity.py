"""Password identity — sign-up and authentication (SPEC-010 §6 Step 3, D3/D9/O1).

Kept out of the route because the two functions here carry the spec's hardest constraint, and
it is not a routing concern:

**`authenticate` must cost the same whether or not the email exists.** Every early return in
this file is a decision about that. The natural implementation —

    user = lookup(email)
    if user is None:
        return None            # microseconds
    return verify(password, user.password_hash)   # ~100ms

— answers "does this account exist?" in its response time, for anyone with a list of addresses
and a stopwatch. That is the first step of a credential-stuffing run, and no error message
needs to leak for it to work.

So `authenticate` always calls `verify_password`, passing `None` when there is no user, and
`verify_password` derives anyway (D9, `passwords.py`). The test that guards this counts KDF
invocations rather than measuring the clock — a wall-clock assertion on a shared CI box is
flaky, and a flaky security test gets disabled.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from mihomes.auth.passwords import hash_password, needs_rehash, verify_password
from mihomes.models.user import User

#: O2's documented default. **12 characters, no composition rules** — length beats character
#: classes, and mandatory symbols push people towards `Password1!` and a sticky note. No
#: breach-corpus check yet; that is U3, and it is the thing that would actually help.
MIN_PASSWORD_LENGTH = 12


class SignupError(Exception):
    """Base for the two refusals below, so a caller can catch either."""


class PasswordTooShort(SignupError):
    def __init__(self) -> None:
        super().__init__(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


class EmailAlreadyRegistered(SignupError):
    """A password account already exists for this address."""

    def __init__(self) -> None:
        super().__init__("An account with that email already exists.")


class EmailRegisteredWithGoogle(SignupError):
    """A **Google** account already exists for this address, and it has no password.

    **This refusal is what stops signup from forking an identity.** `find_password_user`
    deliberately matches only rows that already have a password — it has to, or login would
    verify against a `None` hash and lock a Google user out forever. But that made it blind
    here: a Google-only row was invisible to signup's guard, so signing up with an address that
    already had a Google account inserted a *second* `users` row rather than refusing. Both
    rows were legal — `uq_users_email_password` is partial (`WHERE password_hash IS NOT NULL`)
    and `google_sub` was NULL on the new row, and NULLs do not collide.

    The consequence was not a duplicate row, it was a **silently orphaned account**. The new
    row carried no membership, so it owned nothing: the user signed in, landed on an identity
    with no account, and every page answered 403 "No account selected" while their real
    account — and its properties — sat on the row they were no longer using. Measured on a live
    dev database: two rows for one address, the Google one owning the estate, the password one
    owning nothing.

    **Named separately from `EmailAlreadyRegistered` because the remedy differs**: that one
    means "you already have this password account, sign in"; this one means "sign in with
    Google instead". Telling someone to reset a password they never set is a dead end.

    This does confirm to a stranger that an address holds a Google account. Accepted
    deliberately: signup already confirms existence via `EmailAlreadyRegistered` (see
    `routes/password.py:signup`'s docstring on the asymmetry — the login form must not confirm
    an address, the signup form cannot avoid it), so the oracle is not new, and the
    alternative is minting the orphan. Dual-identity linking — writing a password onto the
    existing Google row — is SPEC-010 U2 and stays out of scope: attaching a credential to an
    existing account from an unauthenticated POST is an account-takeover primitive unless it is
    gated behind proof of mailbox control.
    """

    def __init__(self) -> None:
        super().__init__(
            "That email is already registered. Sign in with Google instead."
        )


def _normalise(email: str) -> str:
    return email.strip().lower()


def find_password_user(db: DbSession, email: str) -> User | None:
    """The one lookup that keys on email rather than `google_sub`.

    Case-folded, and restricted to rows that actually have a password — matching
    `uq_users_email_password` exactly. Without the `password_hash IS NOT NULL` half this would
    return a Google-only user, and the caller would then verify a password against `None` and
    refuse them forever with no way to tell why.
    """
    return db.execute(
        select(User).where(
            func.lower(User.email) == _normalise(email),
            User.password_hash.isnot(None),
        )
    ).scalar_one_or_none()


def _find_any_user_by_email(db: DbSession, email: str) -> User | None:
    """Any row on this address, with or without a password. **Signup's guard only.**

    Kept private and separate from `find_password_user` so the two cannot be confused at a call
    site: this one must never be used by login or reset. Returning a Google-only row there is
    what would have the caller verify a password against `None` and refuse a real user forever
    with no way to tell why — the failure `find_password_user`'s `password_hash IS NOT NULL`
    filter exists to prevent.

    `.first()`, not `scalar_one_or_none()`: the duplicate rows this guard prevents already
    exist in databases created before it, and a guard that raises `MultipleResultsFound` on
    exactly the data it is meant to detect would turn a clear refusal into a 500.
    """
    return db.execute(
        select(User).where(func.lower(User.email) == _normalise(email)).limit(1)
    ).scalars().first()


def create_password_user(db: DbSession, *, email: str, password: str, name: str | None = None) -> User:
    """Create a user authenticated by password. **Does not commit.**

    Raises `EmailAlreadyRegistered` if a password account exists, or
    `EmailRegisteredWithGoogle` if the address is held by a Google-only row.

    **Both refusals are needed, and the second has no database backstop.** For the first, the
    partial unique index is the real enforcement and this check only supplies the message — a
    concurrent insert still fails at the database, which is the correct place for it to fail.
    For the second there is *nothing* underneath: `uq_users_email_password` is partial and does
    not apply to a row whose `password_hash` is NULL, so a duplicate address is legal at the
    schema level and this guard is the only thing standing between signup and a forked
    identity. See `EmailRegisteredWithGoogle` for what that fork cost.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShort()

    email = _normalise(email)
    if find_password_user(db, email) is not None:
        raise EmailAlreadyRegistered()

    # Deliberately **not** `find_password_user`: that helper filters to rows which already have
    # a password, which is exactly the blindness being fixed. Any row on this address means
    # signup must refuse — inserting a second one strands whichever row holds the membership.
    if _find_any_user_by_email(db, email) is not None:
        raise EmailRegisteredWithGoogle()

    user = User(
        # NULL, not a placeholder. `google_sub` is unique, so a sentinel string would collide
        # on the second password user; NULLs do not collide in Postgres (D6).
        google_sub=None,
        email=email,
        name=name or None,
        password_hash=hash_password(password),
        password_set_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


def authenticate(db: DbSession, *, email: str, password: str) -> User | None:
    """Return the user on a correct password, `None` otherwise — **at the same cost either way**.

    The `verify_password(password, None)` call on the miss path is not defensive padding; it is
    the criterion (A3/A7, D9). Removing it, or short-circuiting above it, reintroduces the
    account-existence oracle that `test_login_does_not_reveal_whether_an_email_exists` counts
    KDF invocations to detect.

    On success, an outdated hash is upgraded in place (D5). This is the only moment the
    plaintext exists to re-hash with, which is why the cost knob can be raised at all without
    invalidating every stored password at once.
    """
    user = find_password_user(db, email)
    stored = user.password_hash if user is not None else None

    if not verify_password(password, stored):
        return None

    # Unreachable in practice — `verify_password` returns False for a None stored hash — but
    # asserting it keeps the contract explicit for anyone editing the branch above.
    if user is None:  # pragma: no cover - defensive
        return None

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        user.password_set_at = datetime.now(timezone.utc)

    return user


def set_password(db: DbSession, user: User, password: str) -> None:
    """Set or replace a password. **Does not commit.**

    Used by the reset flow (G5) and by an invitee accepting without Google (G6). Enforces the
    same length rule as signup — a reset is not a way around the policy.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShort()
    user.password_hash = hash_password(password)
    user.password_set_at = datetime.now(timezone.utc)
    db.flush()
