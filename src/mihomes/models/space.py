"""Space model — rooms/areas within properties."""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TimestampMixin


class Space(Base, TimestampMixin, SlugMixin):
    __tablename__ = "spaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    space_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    zone_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("zones.id"), index=True, nullable=True)

    property = relationship("Property", back_populates="spaces")
    zone = relationship("Zone", back_populates="spaces")
