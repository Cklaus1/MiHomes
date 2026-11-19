"""Vendor rating model — quality scores for vendor work."""

import uuid
from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin


class VendorRating(Base, TimestampMixin, TenantOwned):
    __tablename__ = "vendor_ratings"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vendors.id"), nullable=False)
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=True)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=True)
    quality_score: Mapped[int] = mapped_column(Integer, nullable=False)
    reliability_score: Mapped[int] = mapped_column(Integer, nullable=False)
    # L9: cost/communication are optional dimensions. An unrated dimension is
    # stored as NULL rather than fabricated from another score.
    cost_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    communication_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rated_date: Mapped[date] = mapped_column(Date, nullable=False)

    vendor = relationship("Vendor")
    property = relationship("Property")
