"""Membership — who may act inside an account, and as what (SPEC-002 §4.2).

Two constraints carry the design:

- `UNIQUE (account_id, user_id)` — a person joins an account once.
- **A partial unique index for exactly one ACTIVE owner (D4/A2).** The predicate is
  the whole point: a plain `UNIQUE (account_id, role)` would forbid two admins, and
  a partial index without the status clause would let a *revoked* owner block
  appointing a new one.

`status` is `active | revoked` only (D6). **N7: never add `'invited'`** — an invitee
has no `user_id` yet, which this table requires. Pending invitations live in
`invites`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned


class Membership(Base, TenantOwned):
    __tablename__ = "memberships"

    # account_id comes from TenantOwned — identical to what §4.2 spells out inline
    # (PGUUID, FK to accounts.id ON DELETE CASCADE, NOT NULL, indexed). Declared
    # once, via the mixin, so there is one definition to change.
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)     # owner | admin | staff
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    # D6: active | revoked ONLY. No 'invited' — pending invitations live in `invites`.
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("account_id", "user_id", name="uq_membership_account_user"),
        # D4: exactly one active owner per account.
        Index("uq_membership_one_owner", "account_id", unique=True,
              postgresql_where=text("role = 'owner' AND status = 'active'")),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Membership {self.role}/{self.status}>"


class MembershipPropertyScope(Base, TenantOwned):
    """Staff property whitelist (A4/D5). **Property, not "home"** — N8.

    Zero rows means zero properties visible (`ONBOARDING` §2): fail closed. A staff
    member with no scope rows sees nothing rather than everything, which is the
    safe direction for a mistake in either the UI or the seeding code.
    """

    __tablename__ = "membership_property_scopes"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "membership_id", "property_id", name="uq_scope_membership_property"
        ),
    )
