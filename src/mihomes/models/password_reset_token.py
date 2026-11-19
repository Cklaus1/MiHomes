"""PasswordResetToken — GLOBAL, and the fifth instance of a settled pattern (SPEC-010 §4.3).

**GLOBAL, not tenant-owned**, for the same reason `users` and `sessions` are: a password reset
happens *before* sign-in, so there is no account context to scope to. A tenant policy here would
return zero rows and break the thing it is protecting.

That is a different justification from `email_suppressions`, the other recent global table: that
one is global because suppression belongs to an ADDRESS rather than an account. This one is
global because it is read before an account is known. Worth distinguishing — the two reasons
imply different things about what may safely be added later.

## The raw token is never stored

Only `sha256(raw)`. The raw value is returned once, goes into the email, and is unrecoverable
afterwards — so a database leak yields no usable reset links.

**sha256 here is correct, and it is the opposite call from `auth/passwords.py` next door.** That
module deliberately uses a slow, salted KDF; this one deliberately does not. The difference is
the input: a reset token is 256 bits of `secrets` output, so there is no dictionary to attack
and a slow hash would only make every verification slower. A password is human-chosen and needs
scrypt. Four tables already make this exact call — `invites`, `sessions`, `waitlist`,
`gateway_link_tokens` — and this is the fifth.

## TTL: 1 hour, not the invite's 7 days

A reset link is a live credential for a *specific existing account*, and it lands in an inbox
that may itself be the thing that was compromised. An invite grants access to an account that
has not been set up yet, which is a smaller prize and needs a longer window because it waits on
a human to act.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base


class PasswordResetToken(Base):
    """GLOBAL — read before any account context exists. No account_id, no tenant RLS."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    # CASCADE: a deleted person's pending reset links must not outlive them. Unlike the
    # `gateway_link_tokens` -> `memberships` case this needs no argument — there is no
    # meaningful reading of a reset token whose user is gone.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # sha256 hex of the raw token. 64 chars, never the raw value.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set on redemption. Single-use is enforced by checking this is NULL, not by deleting the
    # row: a used token that still exists can be told apart from one that never existed, which
    # matters when someone reports a link that "didn't work".
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PasswordResetToken user={self.user_id} used={self.used_at is not None}>"
