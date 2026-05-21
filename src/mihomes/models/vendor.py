"""Vendor model — contractors and service providers."""

from sqlalchemy import Boolean, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.models import Base, SlugMixin, TimestampMixin


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
