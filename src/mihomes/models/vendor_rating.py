"""Vendor rating model — quality scores for vendor work."""

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, TimestampMixin


class VendorRating(Base, TimestampMixin):
    __tablename__ = "vendor_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=False)
    work_order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("work_orders.id"), nullable=True)
    property_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("properties.id"), nullable=True)
    quality_score: Mapped[int] = mapped_column(Integer, nullable=False)
    reliability_score: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_score: Mapped[int] = mapped_column(Integer, nullable=False)
    communication_score: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rated_date: Mapped[date] = mapped_column(Date, nullable=False)

    vendor = relationship("Vendor")
    property = relationship("Property")
