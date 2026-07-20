"""Book model — tracks books by location across the estate."""

import enum

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TimestampMixin


class BookCondition(str, enum.Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    DAMAGED = "damaged"


class Book(Base, TimestampMixin, SlugMixin):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str | None] = mapped_column(String(300), nullable=True)
    genre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    isbn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    condition: Mapped[BookCondition] = mapped_column(Enum(BookCondition), default=BookCondition.GOOD)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    space_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("spaces.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    property = relationship("Property")
    space = relationship("Space")
