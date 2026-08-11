"""Book model — tracks books by location across the estate."""

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin


class BookCondition(str, enum.Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    DAMAGED = "damaged"


class Book(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "books"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str | None] = mapped_column(String(300), nullable=True)
    genre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    isbn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    condition: Mapped[BookCondition] = mapped_column(Enum(BookCondition), default=BookCondition.GOOD)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("spaces.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    property = relationship("Property")
    space = relationship("Space")
