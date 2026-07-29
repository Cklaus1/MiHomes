"""Consumable inventory model — tracks stock levels and reorder needs."""

import enum
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TimestampMixin
from mihomes.type.money import Money


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
    low_stock_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Money, nullable=True)
    last_ordered_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[ConsumableStatus] = mapped_column(
        Enum(ConsumableStatus), default=ConsumableStatus.OK, nullable=False
    )
    last_updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    property = relationship("Property")
    price_entries: Mapped[list["ConsumablePriceEntry"]] = relationship(
        "ConsumablePriceEntry", back_populates="consumable", cascade="all, delete-orphan"
    )


class ConsumablePriceEntry(Base):
    __tablename__ = "consumable_price_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    consumable_id: Mapped[int] = mapped_column(Integer, ForeignKey("consumables.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[float] = mapped_column(Money, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    entry_type: Mapped[str] = mapped_column(String(50), default="purchase")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    consumable: Mapped["Consumable"] = relationship("Consumable", back_populates="price_entries")
