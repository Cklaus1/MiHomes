"""TelegramChatBinding — a Telegram group bound to one account (group binding, 2026-10-23).

**Why a chat, not only people.** The webhook resolves a message's account from its *sender's* link
(`telegram_links`), which requires every sender to have a MiHomes login. A household group such as
Belle's is mostly staff without logins, so under sender-only resolution their messages were
refused. An owner or admin who has linked themselves sends `/linkchat` in the group; from then on
every message in that chat belongs to this account. Senders without a link are processed as
*untrusted* (M27: no money, PTO or resolution actions) and are never prompted in the group.

**One chat, one account** — `UNIQUE (chat_id)` across accounts, not per account: a group cannot
belong to two estates, and a second account trying to bind it is refused rather than guessed.
The lookup by `chat_id` happens before any account is bound (that lookup is how the account is
found), under the bootstrap policy `rls.GATEWAY_BOOTSTRAP_POLICIES["telegram_chat_bindings"]`.

`bound_by_membership_id` records who bound it and is `SET NULL` on revocation: the group stays
bound to the estate when the person who bound it leaves — it is the estate's group, not theirs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned


class TelegramChatBinding(Base, TenantOwned):
    __tablename__ = "telegram_chat_bindings"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bound_by_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("memberships.id", ondelete="SET NULL"),
        nullable=True,
    )
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("chat_id", name="uq_telegram_chat_binding_chat"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TelegramChatBinding chat={self.chat_id}>"
