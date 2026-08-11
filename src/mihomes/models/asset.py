"""Asset model — tracked property assets (appliances, vehicles, valuables, etc.)."""

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin
from mihomes.type.money import Money


class AssetType(str, enum.Enum):
    APPLIANCE = "appliance"
    VEHICLE = "vehicle"
    VALUABLE = "valuable"
    CONSUMABLE = "consumable"
    EQUIPMENT = "equipment"


class AssetCondition(str, enum.Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


class PriceEntryType(str, enum.Enum):
    PURCHASE = "purchase"
    VALUATION = "valuation"
    ESTIMATE = "estimate"


class Asset(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    asset_type: Mapped[AssetType] = mapped_column(Enum(AssetType), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("spaces.id"), nullable=True)
    make: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[float | None] = mapped_column(Money, nullable=True)
    warranty_expires: Mapped[date | None] = mapped_column(Date, nullable=True)
    condition: Mapped[AssetCondition] = mapped_column(Enum(AssetCondition), default=AssetCondition.GOOD)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    vehicle_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    valuable_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    equipment_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Lifecycle / capital planning
    install_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_lifespan_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    replacement_cost_estimate: Mapped[float | None] = mapped_column(Money, nullable=True)
    last_serviced: Mapped[date | None] = mapped_column(Date, nullable=True)

    property = relationship("Property")
    space = relationship("Space")
    price_entries: Mapped[list["PriceEntry"]] = relationship(
        "PriceEntry", back_populates="asset", cascade="all, delete-orphan"
    )


class PriceEntry(Base, TenantOwned):
    __tablename__ = "asset_price_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assets.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[float] = mapped_column(Money, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    entry_type: Mapped[str] = mapped_column(String(50), default="purchase")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    asset: Mapped["Asset"] = relationship("Asset", back_populates="price_entries")
