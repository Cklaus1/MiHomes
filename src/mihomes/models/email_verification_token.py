"""EmailVerificationToken — GLOBAL, the confirm-your-address link (2026-11-13).

The `password_reset_tokens` shape exactly: only the sha256 of the raw token is stored, the raw
value lives in one email, and `used_at` makes it single-use. GLOBAL for the same reason: it is
read before sign-in, when there is no account to scope to. An unverified user never gets a session.

**24 hours**, between the reset link's one hour and the invite's seven days. Clicking this link
signs the person in, so it is a credential like a reset link. But it guards a brand-new, empty
account rather than an existing one, and it is often opened later on another device than the
one used to sign up.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base


class EmailVerificationToken(Base):
    """GLOBAL — read before any account context exists. No account_id, no tenant RLS."""

    __tablename__ = "email_verification_tokens"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<EmailVerificationToken user={self.user_id} used={self.used_at is not None}>"
