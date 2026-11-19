"""AI conversation model — history of AI advisory sessions."""

import uuid

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin


class AIConversation(Base, TimestampMixin, TenantOwned):
    __tablename__ = "ai_conversations"
    __table_args__ = (
        Index("ix_ai_conv_account_session", 'account_id', 'session_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    session_id: Mapped[str] = mapped_column(String(50), nullable=False)
    session_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    ai_response: Mapped[str] = mapped_column(Text, nullable=False)
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(50), default="claude")
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
