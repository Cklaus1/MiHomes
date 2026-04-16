"""Property and Space models."""

import enum
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TimestampMixin


class PropertyType(str, enum.Enum):
    PRIMARY = "primary"
    VACATION = "vacation"
    SEASONAL = "seasonal"
    INVESTMENT = "investment"
    RENTAL = "rental"
    ESTATE = "estate"
    OTHER = "other"


class PropertyStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    CARETAKER_MODE = "caretaker-mode"
    UNDER_RENOVATION = "under-renovation"


class Property(Base, TimestampMixin, SlugMixin):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_type: Mapped[PropertyType] = mapped_column(
        Enum(PropertyType), default=PropertyType.OTHER
    )
    status: Mapped[PropertyStatus] = mapped_column(
        Enum(PropertyStatus), default=PropertyStatus.OPEN
    )
    climate_zone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sqft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    features: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    occupied: Mapped[bool] = mapped_column(Boolean, default=False)
    occupied_since: Mapped[date | None] = mapped_column(Date, nullable=True)
    occupied_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    spaces = relationship("Space", back_populates="property", cascade="all, delete-orphan")
