"""Zone model — generalized areas within a property (e.g., Upstairs, Exterior Back)."""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TimestampMixin


class Zone(Base, TimestampMixin, SlugMixin):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)

    property = relationship("Property")
    spaces = relationship("Space", back_populates="zone")
    tasks = relationship("Task", back_populates="zone")
