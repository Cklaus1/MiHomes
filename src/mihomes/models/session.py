"""Session — server-side sessions. GLOBAL, not tenant-owned (SPEC-002 §4.2, D3).

**This is the most load-bearing "global" in the schema.** The auth middleware reads
this row *before* account context exists — it is where the current account comes
from. A tenant policy here would return zero rows and lock every user out of the
product, which is why D3 lists it alongside `users`.

`current_account_id` is nullable on purpose: a user who has just signed in has not
picked an account yet, and one who belongs to several switches between them.

**Only the hash is stored.** The raw session id goes to the cookie and never to the
database — same discipline as the invite tokens (`ONBOARDING` §10) and SPEC-001's
confirm token. A database leak must not hand over live sessions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base


class Session(Base):
    """GLOBAL (D3) — no account_id, no tenant RLS."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    session_id_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Nullable: freshly signed in and no account chosen yet, or between switches.
    current_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Session user={self.user_id} account={self.current_account_id}>"
