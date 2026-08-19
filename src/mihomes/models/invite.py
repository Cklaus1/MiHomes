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

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned


class Invite(Base, TenantOwned):
    __tablename__ = "invites"
    __table_args__ = (
        Index("ix_invites_account_email", 'account_id', 'email'),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)   # admin | staff
    # Deliberately NOT composite with account_id, and deliberately globally
    # unique: an invite is accepted by presenting this token *before* the
    # recipient belongs to any account, so the lookup cannot supply one. Making
    # it (account_id, token_hash) would also let two accounts mint the same
    # hash. Do not "fix" this in a leads-with-account_id sweep.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # The property scopes this invite will grant on acceptance (SPEC-003 D3/A21).
    #
    # **Added by SPEC-003 G12, because §5's `create_invite(..., property_ids)` had nowhere to put
    # them.** A staff invite is rejected outright with zero properties (D3: "fail closed, never
    # 'all'"), so the set has to survive from creation until acceptance — the invitee may not sign
    # up for days, and the inviter is not present to re-state it. On acceptance these become
    # `membership_property_scopes` rows and this column stops being the authority.
    #
    # JSON rather than a join table: an invite is short-lived (7 days) and its scopes are never
    # queried across invites — only read once, by the acceptance that consumes them.
    property_ids: Mapped[list] = mapped_column(
        JSON, nullable=False, server_default=text("'[]'::json")
    )
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
