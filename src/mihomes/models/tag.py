"""Tag model — user-defined labels attachable to any entity."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.models import Base, TimestampMixin


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)


class TagAssignment(Base, TimestampMixin):
    __tablename__ = "tag_assignments"
    __table_args__ = (
        UniqueConstraint("tag_id", "entity_type", "entity_id", name="uq_tag_assignment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tag_id: Mapped[int] = mapped_column(Integer, ForeignKey("tags.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
