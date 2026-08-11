"""User — GLOBAL, not tenant-owned (SPEC-002 §4.2, D3).

A person exists independent of any account, and this row is read *before* account
context exists. A tenant policy here would break sign-in outright.

`google_sub` is the identity key, not `email`: Google's subject is stable, while an
email address can change. Keying on email would silently orphan someone's
memberships the day they change their Google address.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base


class User(Base):
    """GLOBAL — a person exists independent of any account. No account_id, no tenant RLS."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)  # display only, may change
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # A3

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email}>"
