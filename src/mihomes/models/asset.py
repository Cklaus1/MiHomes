"""Asset model — tracked property assets (appliances, vehicles, valuables, etc.)."""

import enum
from datetime import date

from sqlalchemy import Boolean, Date, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TimestampMixin


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


class Asset(Base, TimestampMixin, SlugMixin):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    asset_type: Mapped[AssetType] = mapped_column(Enum(AssetType), nullable=False)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    space_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("spaces.id"), nullable=True)
    make: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    replacement_cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_serviced: Mapped[date | None] = mapped_column(Date, nullable=True)

    property = relationship("Property")
    space = relationship("Space")
