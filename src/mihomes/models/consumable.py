"""Consumable inventory model — tracks stock levels and reorder needs."""

import enum

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TimestampMixin


class ConsumableStatus(str, enum.Enum):
    OK = "ok"
    LOW = "low"
    OUT = "out"
    ORDERED = "ordered"


class Consumable(Base, TimestampMixin, SlugMixin):
    __tablename__ = "consumables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    quantity_in_stock: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity_to_order: Mapped[float | None] = mapped_column(Float, nullable=True)
    par_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[ConsumableStatus] = mapped_column(
        Enum(ConsumableStatus), default=ConsumableStatus.OK, nullable=False
    )
    last_updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    property = relationship("Property")
