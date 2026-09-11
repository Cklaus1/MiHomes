"""The signed-in user's own profile — display name and sign-in email.

**Separate from `services/account.py` because the subjects are different.** That one renames the
*estate*, which every member sees; this changes *one person's* record. They sit next to each
other on `/settings` and share nothing else — notably, renaming an estate is an owner/admin
action while editing your own name is not, so folding them together would have meant one
permission gate answering two unrelated questions.

**On changing the email — what this does and does not do.** The email is the sign-in credential
(`uq_users_email_password` is on `lower(email)`), so a typo here is a lockout: the old address
stops working and the new one was never real. The correct shape is a confirmation link mailed to
the *new* address, switching only when clicked — a token table, a mail template and an expiry,
which is a larger build than this.

What is here instead: the change is immediate, but guarded so the realistic failures are refused
rather than absorbed — the format is validated, an address already registered is rejected, and
the current password must be supplied so a borrowed session cannot silently move the account to
an attacker's address. **The residual is real and deliberate**: nothing proves the new address
is one you can receive mail at, so a well-formed typo will still lock you out. Recorded here
rather than in a commit message because this is where someone will look before extending it.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mihomes.auth.passwords import verify_password
from mihomes.models.user import User
from mihomes.services.audit import record_change

__all__ = ["MAX_NAME_LENGTH", "ProfileError", "update_profile"]

#: Matches the column.
MAX_NAME_LENGTH = 200

#: Deliberately permissive. A stricter pattern rejects addresses that are perfectly valid —
#: `+` tags, new TLDs, quoted locals — and the only real proof an address works is mail
#: arriving at it, which this does not do. So this catches the fat-finger cases (no `@`, no
#: domain, whitespace) and nothing more, rather than pretending to validate deliverability.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProfileError(ValueError):
    """The proposed change is not usable. Message is safe to show the user."""


def update_profile(
    session: Session,
    user_id: uuid.UUID,
    *,
    name: str,
    email: str,
    current_password: str | None = None,
) -> User:
    """Update the signed-in user's name and email. **Does not commit.**

    `current_password` is required **only when the email actually changes** — renaming yourself
    is a display tweak, moving your sign-in address is not. Asking for a password to change a
    display name trains people to type it for trivial things, which is its own hazard.
    """
    user = session.get(User, user_id)
    if user is None:
        raise ProfileError("That account no longer exists.")

    cleaned_name = name.strip()
    if not cleaned_name:
        raise ProfileError("Your name cannot be empty.")
    if len(cleaned_name) > MAX_NAME_LENGTH:
        raise ProfileError(
            f"That name is too long — {len(cleaned_name)} characters, "
            f"and the limit is {MAX_NAME_LENGTH}."
        )

    cleaned_email = email.strip().lower()
    if not _EMAIL.match(cleaned_email):
        raise ProfileError("That does not look like an email address.")

    email_changed = cleaned_email != (user.email or "").lower()

    if email_changed:
        # **Password first, before anything is written.** This is the check that stops a
        # borrowed session — a shared machine, an unlocked laptop — from quietly moving the
        # account to an address its owner does not control.
        if user.password_hash is None:
            raise ProfileError(
                "This account signs in with Google, so its email is managed there and "
                "cannot be changed here."
            )
        if not current_password or not verify_password(current_password, user.password_hash):
            raise ProfileError(
                "That password is not correct, so the email was not changed."
            )

        # Checked here for the message; `uq_users_email_password` is the real enforcement and
        # still catches a concurrent insert, which is the correct place for that to fail.
        taken = session.execute(
            select(User.id).where(
                func.lower(User.email) == cleaned_email,
                User.id != user.id,
                User.password_hash.isnot(None),
            )
        ).first()
        if taken:
            raise ProfileError("Another account already uses that email address.")

    previous_name, previous_email = user.name, user.email
    if cleaned_name == previous_name and not email_changed:
        # Nothing changed — no write, no audit row. A form re-submit is not an event.
        return user

    user.name = cleaned_name
    user.email = cleaned_email
    session.flush()

    changes: dict[str, dict[str, str | None]] = {}
    if cleaned_name != previous_name:
        changes["name"] = {"from": previous_name, "to": cleaned_name}
    if email_changed:
        changes["email"] = {"from": previous_email, "to": cleaned_email}

    # `users` is GLOBAL, but `audit_log` is tenant-owned — so the write above needs no tenant
    # context and the record of it does. The caller supplies that; see `routes/settings.py`.
    record_change(
        session,
        entity_type="user",
        entity_id=user.id,
        # `audit_log.action` is `varchar(10)`, and every existing value is a short verb —
        # `rename`, `replace`, `inspect`. `profile_update` was 14 characters and failed with
        # `StringDataRightTruncation` at flush; `entity_type="user"` already carries the
        # subject, so the verb does not need to repeat it.
        action="update",
        changes=changes,
    )

    # **Flushed here, not left to the request's commit.** `record_change` only `session.add`s;
    # the INSERT happens at flush, and `_stamp_tenant_on_insert` reads `current_account` at
    # *that* moment to fill `account_id`. Deferring it to `get_db`'s commit runs the flush after
    # the caller's `account_context` block has exited, so the ContextVar is unset again and the
    # stamp raises `LookupError` — which surfaces as a 500 on a request that had already
    # succeeded. Measured exactly that way.
    session.flush()
    return user
