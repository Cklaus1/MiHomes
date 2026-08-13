"""Tag model — user-defined labels attachable to any entity."""

import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin


class Tag(Base, TimestampMixin, TenantOwned):
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("account_id", "name", name="uq_tags_account_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class TagAssignment(Base, TimestampMixin, TenantOwned):
    __tablename__ = "tag_assignments"
    __table_args__ = (
        Index("ix_tag_assign_account_entity", 'account_id', 'entity_type', 'entity_id'),
        UniqueConstraint("tag_id", "entity_type", "entity_id", name="uq_tag_assignment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tags.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
