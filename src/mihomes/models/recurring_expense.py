"""Recurring expense model — repeating financial items."""

import enum
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin
from mihomes.type.money import Money


class ExpenseFrequency(str, enum.Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    CUSTOM_WEEKS = "custom_weeks"
    CUSTOM_MONTHS = "custom_months"


class RecurringExpense(Base, TimestampMixin, TenantOwned):
    __tablename__ = "recurring_expenses"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[float] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    frequency: Mapped[ExpenseFrequency] = mapped_column(Enum(ExpenseFrequency), nullable=False)
    interval_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vendors.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_generated: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    property = relationship("Property")
    vendor = relationship("Vendor")
