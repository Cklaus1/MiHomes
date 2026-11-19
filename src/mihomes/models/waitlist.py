"""Waitlist model — Phase 0 signup capture. GLOBAL: no account_id (see SPEC-001 D4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base


class Waitlist(Base):
    __tablename__ = "waitlist"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )

    # Identity. Stored lowercased+stripped; see normalize_email() in services/waitlist.py.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Light qualification (GTM section 3 — optional, never gates the signup).
    num_homes: Mapped[str | None] = mapped_column(String(10), nullable=True)   # '1' | '2-3' | '4+'
    has_staff: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Attribution (GTM section 3 segmentation table).
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)     # 'form' | 'google'
    utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    referred_by: Mapped[str | None] = mapped_column(String(320), nullable=True)

    # Double opt-in (D7). Raw token lives only in the email; we store its SHA-256.
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    confirm_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirm_send_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Diagnostics. Not shown to users.
    signup_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)   # INET6 max length
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "confirmed" if self.confirmed_at else "pending"
        return f"<Waitlist {self.email} ({state})>"
