"""Consumable inventory model — tracks stock levels and reorder needs."""

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin
from mihomes.type.money import Money


class ConsumableStatus(str, enum.Enum):
    OK = "ok"
    LOW = "low"
    OUT = "out"
    ORDERED = "ordered"


class Consumable(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "consumables"
    __table_args__ = (
        UniqueConstraint("account_id", "slug", name="uq_consumables_account_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
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


class ConsumablePriceEntry(Base, TenantOwned):
    __tablename__ = "consumable_price_entries"
    __table_args__ = (
        Index("ix_consumable_prices_account_item", 'account_id', 'consumable_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    consumable_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("consumables.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[float] = mapped_column(Money, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    entry_type: Mapped[str] = mapped_column(String(50), default="purchase")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    consumable: Mapped["Consumable"] = relationship("Consumable", back_populates="price_entries")
