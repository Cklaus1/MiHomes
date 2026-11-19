"""Sign-in methods on an existing account — add a password, change it, connect Google.

**Resolves SPEC-010 U2 and overrides N7 (user decision, 2026-10-19).** One person may now hold
both a password and a Google identity on the same `users` row. N7 refused this because linking
"needs a verification flow of its own"; the verification is what this module is made of:

- **Every change starts from a signed-in session.** Linking from an *unauthenticated* flow is
  the pre-hijacking primitive `oidc.PasswordAccountExists` refuses — a squatter registers your
  address with a password, and your later Google sign-in merges into their row. Here the person
  has already proven control of the account they are changing.
- **Connecting Google requires the Google address to match the account's.** A verified Google
  address proves mailbox control, which password signup never checked. Without the match, a
  stolen session cookie could link the thief's own Google account as a permanent way back in.
- **Adding a password requires a recent sign-in** (`RECENT_SIGNIN`). A Google-only user has no
  password to re-enter, so the proof is freshness: the session row is minted at every sign-in
  (`establish_session` rotates it), so its `created_at` is when they last authenticated.
- **Changing a password requires the current one**, and is throttled like login — otherwise a
  stolen session is an unthrottled oracle for the password.

Nothing here commits; the routes own the transaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from mihomes.auth.oidc import IdentityClaims
from mihomes.auth.password_identity import PasswordTooShort, set_password
from mihomes.auth.passwords import verify_password
from mihomes.auth.sessions import hash_session_id
from mihomes.models.session import Session as SessionRow
from mihomes.models.user import User

__all__ = [
    "NOTICES",
    "RECENT_SIGNIN",
    "SignInMethodError",
    "add_password",
    "change_password",
    "link_google",
    "message_for",
    "notify_sign_in_change",
    "signed_in_recently",
]

#: How recently a Google-only user must have signed in to add a password. Long enough to read
#: the form and choose one; short enough that an unattended session is not enough.
RECENT_SIGNIN = timedelta(minutes=10)


#: Every refusal, by code. **Codes, not text, cross a redirect**: the OAuth callback reports
#: back to Settings through the query string, and a page that renders `?error=<any text>` lets
#: anyone put words of their choosing on a signed-in page. `{email}` is filled from the account.
MESSAGES = {
    "password_exists": "You already have a password. Use “Change password” instead.",
    "password_mismatch": "The two passwords don't match.",
    "password_short": "That password is too short.",
    "password_clash": (
        "Another MiHomes account already signs in with this email and a password, so a "
        "password can't be added here. Contact support to merge the two."
    ),
    "no_password": "You don't have a password yet.",
    "wrong_current": "Your current password is incorrect.",
    "google_exists": "A Google account is already connected.",
    "google_mismatch": (
        "That Google account uses a different email. Choose the Google account for {email}."
    ),
    "google_taken": "That Google account already signs in to a different MiHomes account.",
    "reauth_needed": (
        "For your security, confirm it's you with Google before adding a password."
    ),
    "reauth_mismatch": (
        "That was a different Google account. Confirm with the Google account for {email}."
    ),
    "flow_lost": "Your session changed during the Google step. Please try again.",
    "too_many": "Too many attempts. Wait a few minutes and try again.",
}

#: Success banners, by code, for the same reason.
NOTICES = {
    "google_connected": "Google sign-in is connected. You can now sign in either way.",
    "password_added": "Password added. You can now sign in with your email and password too.",
    "password_changed": "Password changed. Your other devices were signed out.",
    "confirmed": "Confirmed. You can add a password now.",
}


class SignInMethodError(Exception):
    """A refusal the person can act on, identified by a `MESSAGES` code."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        assert code in MESSAGES, code
        self.code = code
        # `detail` overrides the stock text when the cause carries its own wording (the
        # password length rule lives in `password_identity`, not here).
        self.detail = detail
        super().__init__(detail or MESSAGES[code])

    def message(self, email: str) -> str:
        return self.detail or MESSAGES[self.code].format(email=email)


def message_for(code: str | None, email: str) -> str | None:
    """The text for a code from the query string, or None for an unknown one."""
    if code in MESSAGES:
        return MESSAGES[code].format(email=email)
    return None


def signed_in_recently(db: DbSession, raw_session: str | None, *, now: datetime | None = None) -> bool:
    if not raw_session:
        return False
    created = db.execute(
        select(SessionRow.created_at).where(
            SessionRow.session_id_hash == hash_session_id(raw_session)
        )
    ).scalar_one_or_none()
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - created <= RECENT_SIGNIN


def _check_new_password(password: str, confirm: str) -> None:
    # Length is `set_password`'s rule (one place for the policy); callers translate its error.
    if password != confirm:
        raise SignInMethodError("password_mismatch")


def add_password(db: DbSession, user: User, password: str, confirm: str) -> None:
    """Give a Google-only user a password. The caller has already checked freshness."""
    if user.password_hash is not None:
        raise SignInMethodError("password_exists")
    _check_new_password(password, confirm)

    # A duplicate password row on this address predates SPEC-010's signup guard. Writing a
    # second password onto the same email would hit `uq_users_email_password` as a 500.
    clash = db.execute(
        select(User.id)
        .where(
            func.lower(User.email) == user.email.strip().lower(),
            User.password_hash.isnot(None),
            User.id != user.id,
        )
        .limit(1)
    ).first()
    if clash is not None:
        raise SignInMethodError("password_clash")

    try:
        set_password(db, user, password)
    except PasswordTooShort as exc:
        raise SignInMethodError("password_short", str(exc)) from None


def change_password(db: DbSession, user: User, current: str, password: str, confirm: str) -> None:
    """Replace an existing password. Throttling is the route's job (it knows the client IP)."""
    if user.password_hash is None:
        raise SignInMethodError("no_password")
    if not verify_password(current, user.password_hash):
        raise SignInMethodError("wrong_current")
    _check_new_password(password, confirm)
    try:
        set_password(db, user, password)
    except PasswordTooShort as exc:
        raise SignInMethodError("password_short", str(exc)) from None


def link_google(db: DbSession, user: User, claims: IdentityClaims) -> None:
    """Attach this verified Google identity to the signed-in user's row."""
    if user.google_sub is not None:
        if user.google_sub == claims.subject:
            return  # Already this Google account — nothing to do, and not an error.
        raise SignInMethodError("google_exists")

    if claims.email.strip().lower() != user.email.strip().lower():
        raise SignInMethodError("google_mismatch")

    # `google_sub` is unique: without this check a sub already on another row is an
    # IntegrityError, i.e. a 500 instead of an explanation.
    elsewhere = db.execute(
        select(User.id).where(User.google_sub == claims.subject, User.id != user.id)
    ).first()
    if elsewhere is not None:
        raise SignInMethodError("google_taken")

    user.google_sub = claims.subject
    if claims.picture and not user.avatar_url:
        user.avatar_url = claims.picture
    db.flush()


def notify_sign_in_change(
    db: DbSession, *, to: str, account_id, change: str, settings_url: str
) -> None:
    """Best-effort security notice; commits its own outbox row. **Never raises**: the change
    must already be committed by the caller, and a
    mail provider that is down must not turn a successful change into an error page.

    Bound to the account when there is one so the message goes through the outbox (D12); with
    none, `EmailService` sends inline.
    """
    import logging

    from mihomes.services.email import EmailService, get_email_provider
    from mihomes.services.email.provider import EmailProviderError
    from mihomes.tenancy import account_context
    from mihomes.tenancy.connection import bind_account_guc

    log = logging.getLogger(__name__)
    try:
        service = EmailService(get_email_provider(), session=db)
        if account_id is None:
            service.send_sign_in_method_changed(to, change=change, settings_url=settings_url)
            return
        with account_context(account_id):
            bind_account_guc(db, account_id)
            service.send_sign_in_method_changed(to, change=change, settings_url=settings_url)
        # Committed here, inside the guard: the outbox row is the only write, and a failure to
        # queue it must not become a 500 after the credential change already landed.
        db.commit()
    except EmailProviderError:
        log.exception("sign-in method notice could not be sent")
    except SQLAlchemyError:
        db.rollback()
        log.exception("sign-in method notice could not be queued")
