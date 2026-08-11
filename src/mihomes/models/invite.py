"""Invite — a pending invitation, tenant-owned (SPEC-002 §4.2).

**A pending row CONSUMES A SEAT** (`PRICING` 3.1 as corrected by A1). That is a
billing-visible fact, not an implementation detail: inviting five people commits
five seats before any of them signs up.

Why this is a separate table rather than a `memberships.status = 'invited'` row
(N7): a membership requires a `user_id`, and an invitee has no user row yet. The
alternatives would be a nullable `user_id` — which breaks
`UNIQUE (account_id, user_id)` — or a fabricated user.

**Hash only, never the raw token** — same discipline as `ONBOARDING` §10 and
SPEC-001's confirm token. A database leak must not yield usable invitations.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned


class Invite(Base, TenantOwned):
    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)   # admin | staff
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    # pending | accepted | revoked | expired
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Invite {self.email} ({self.status})>"
