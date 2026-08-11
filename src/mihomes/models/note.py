"""Note model — polymorphic notes attachable to any entity."""

import uuid

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin


class Note(Base, TimestampMixin, TenantOwned):
    __tablename__ = "notes"
    __table_args__ = (Index("ix_note_entity", "entity_type", "entity_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
