"""Chat notifications when a member is away (SPEC-014 §4.1).

Both tables are per-membership and written only through the caller's own membership (no route
names another member). Every index leads with `account_id` (RLS); a membership belongs to exactly
one account, so `(account_id, membership_id[, endpoint])` is as selective as the spec's
single-column keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, true
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned


class PushSubscription(Base, TenantOwned):
    """One browser's push endpoint for one membership (D5). Unique per (membership, endpoint),
    never globally: a global unique would collide with another account's row RLS hides."""

    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)          # https + allowlisted (D6)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)     # base64url, 65 bytes
    auth: Mapped[str] = mapped_column(String(100), nullable=False)       # base64url, 16 bytes
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("account_id", "membership_id", "endpoint",
                         name="uq_push_subscriptions_member_endpoint"),
    )


class ChatNotifyPref(Base, TenantOwned):
    """Per-membership switches. **No row means defaults** (both on), so the job outer-joins."""

    __tablename__ = "chat_notify_prefs"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False)
    email_digest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    push: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # D2: the created_at cutoff the last digest covered — NOT when it was sent.
    digest_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uq_chat_notify_prefs_member", "account_id", "membership_id", unique=True),
    )
