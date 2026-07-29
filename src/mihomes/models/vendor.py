"""Vendor model — contractors and service providers."""

from sqlalchemy import Boolean, Column, ForeignKey, Integer, JSON, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TimestampMixin

# M14: vendor ↔ property is a many-to-many link, mirroring staff_properties.
# Replaces the former vendors.property_ids JSON blob (normalized in migration
# 9c1f2a7b4d8e). A vendor tagged to a property is reachable from both sides.
vendor_property_association = Table(
    "vendor_properties",
    Base.metadata,
    Column("vendor_id", Integer, ForeignKey("vendors.id"), primary_key=True),
    Column("property_id", Integer, ForeignKey("properties.id"), primary_key=True),
)


class Vendor(Base, TimestampMixin, SlugMixin):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    service_categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    service_areas: Mapped[list | None] = mapped_column(JSON, nullable=True)
    contacts: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{"name","role","phone","email"}]
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    license_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    insurance_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    properties = relationship(
        "Property", secondary=vendor_property_association, backref="vendors"
    )

    @property
    def property_ids(self) -> list[int]:
        """Read-only view of linked property IDs (back-compat for templates)."""
        return [p.id for p in self.properties]
