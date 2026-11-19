"""TelegramLink — SPEC-003 §4.2, D19 (A28, A32).

**Keyed on `memberships`, never on `Staff`** (D19, N6). Two role vocabularies exist and must not
be crossed: `memberships.role` is `owner`/`admin`/`staff` — the capability matrix's vocabulary,
and what D16 means by "staff-level" — while `StaffRole` (`models/staff.py`) is a **job** enum
spanning `RESIDENT`/`OWNER`/`FAMILY_MEMBER`/`ASSOCIATE`. Resolving a sender through `Staff` and
then applying a matrix decision would silently mix them: **a `StaffRole.OWNER` housekeeping record
is not an account owner.**

`TELEGRAM_PRD:129` specifies `telegram_user_id → membership → (account_id, role, home scopes)`,
and `:158`'s *"revoking a membership implicitly revokes the link"* is only true if membership is
the key — which is why `ondelete="CASCADE"` below makes that promise structural rather than
something a code path has to remember (A32).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned


class TelegramLink(Base, TenantOwned):
    __tablename__ = "telegram_links"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("memberships.id", ondelete="CASCADE"),
        nullable=False,
    )
    # BigInteger: Telegram user ids exceed 2^31 and will keep growing. A plain Integer would
    # start rejecting new accounts silently at signup, years after this line was written.
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # One link per sender per account. Not globally unique: the same person may legitimately
        # be a member of two accounts and reach the bot in both.
        UniqueConstraint("account_id", "telegram_user_id", name="uq_telegram_link_account_user"),
        # The lookup index. Deliberately **not** led by `account_id`, unlike every other tenant
        # index here: the bot resolves a sender *before* it knows which account they belong to —
        # that resolution is how the account is discovered. Leading with account_id would leave
        # the only query this table exists to serve unindexed.
        Index("ix_telegram_links_lookup", "telegram_user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TelegramLink tg={self.telegram_user_id}>"
