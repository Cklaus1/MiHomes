"""User — GLOBAL, not tenant-owned (SPEC-002 §4.2, D3).

A person exists independent of any account, and this row is read *before* account
context exists. A tenant policy here would break sign-in outright.

`google_sub` is the identity key **for a Google user**, not `email`: Google's subject is
stable, while an email address can change. Keying on email would silently orphan someone's
memberships the day they change their Google address.

## SPEC-010 — a second kind of identity (D3, D6)

A password user has no Google subject, so `google_sub` is now **nullable**. That is safe
alongside its unique constraint: Postgres does not collide NULLs, so every password user can
carry `google_sub IS NULL` while Google users stay unique among themselves.

**The two identity types are keyed differently, and `email` stays deliberately non-unique.**
A password user *is* keyed on their email — there is nothing else to key them on — but the
uniqueness that enforces lives in a **partial** index covering only rows with a password
(`uq_users_email_password` below), never on the column. Making the column unique table-wide
would break Google identity outright: `test_auth.py:283` asserts that the same address under
two different subjects is two different people, which is correct, because an address can be
reassigned and a Google subject cannot.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base


class User(Base):
    """GLOBAL — a person exists independent of any account. No account_id, no tenant RLS."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    # Nullable since SPEC-010 (D6) — a password user has no Google subject. Still unique:
    # Postgres allows many NULLs under a unique index, so Google identity is untouched.
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    # Display/contact metadata for a Google user, and it may change without breaking identity.
    # For a PASSWORD user it is also the login handle — see `uq_users_email_password` below,
    # which is the only place that uniqueness is enforced, and only for those rows.
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    # `scrypt$n$r$p$salt$hash` (SPEC-010 D5, `auth/passwords.py`). NULL for a Google-only user,
    # which is exactly what `uq_users_email_password` keys off — and what makes
    # `verify_password` take a `str | None` and do the KDF work anyway (D9).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Not decorative: it distinguishes "never had a password" from "set long ago", and it is
    # what a future "your password was changed" notice or rotation policy reads.
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # A3
    # D11 — "persists `last_used_account`". `sessions.current_account_id` is per-session, so a
    # *new* session (a new device, a cleared cookie) has no account to open at. This is that
    # default, and it is the difference between signing in and landing where you were, versus
    # signing in and being asked which of your accounts you meant.
    #
    # `ON DELETE SET NULL`, not CASCADE: deleting an account must not delete the people in it.
    last_used_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # SPEC-010 D3 — two password users cannot share a case-folded email; two GOOGLE users
    # still can. Both halves matter, and the second is what proves the index is partial:
    # a table-wide unique on `email` would satisfy the first and break `test_auth.py:283`.
    #
    # Declared here as well as in migration 0017 because `test_baseline_matches_metadata`
    # autogenerates a diff between `Base.metadata` and the migrated schema — an index present
    # in only one of the two is drift, and fails that gate.
    #
    # `lower(email)` rather than a CITEXT column: nobody expects Alice@ and alice@ to be
    # different logins, and an expression index needs no extension and no column rewrite.
    __table_args__ = (
        Index(
            "uq_users_email_password",
            func.lower(email),
            unique=True,
            postgresql_where=password_hash.isnot(None),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email}>"
