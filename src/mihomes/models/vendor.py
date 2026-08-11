"""Vendor model — contractors and service providers."""

import uuid

from sqlalchemy import JSON, Boolean, Column, ForeignKey, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin

# M14: vendor ↔ property is a many-to-many link, mirroring staff_properties.
# Replaces the former vendors.property_ids JSON blob (normalized in migration
# 9c1f2a7b4d8e). A vendor tagged to a property is reachable from both sides.
# `account_id` is declared BY HAND here, as in staff_properties: TenantOwned is a
# @declared_attr mixin and cannot reach a Core Table. Omitting it would leave this
# join table with no RLS policy and no A1/A21 coverage — see
# mihomes.tenancy.registry, which lists it explicitly for that reason.
vendor_property_association = Table(
    "vendor_properties",
    Base.metadata,
    Column("vendor_id", PGUUID(as_uuid=True), ForeignKey("vendors.id"), primary_key=True),
    Column("property_id", PGUUID(as_uuid=True), ForeignKey("properties.id"), primary_key=True),
    Column(
        "account_id",
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
)


class Vendor(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
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
