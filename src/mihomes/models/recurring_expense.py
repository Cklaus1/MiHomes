"""Recurring expense model — repeating financial items."""

import enum
from datetime import date

from sqlalchemy import Boolean, Date, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, TimestampMixin
from mihomes.type.money import Money


class ExpenseFrequency(str, enum.Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    CUSTOM_WEEKS = "custom_weeks"
    CUSTOM_MONTHS = "custom_months"


class RecurringExpense(Base, TimestampMixin):
    __tablename__ = "recurring_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[float] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    frequency: Mapped[ExpenseFrequency] = mapped_column(Enum(ExpenseFrequency), nullable=False)
    interval_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    vendor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_generated: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    property = relationship("Property")
    vendor = relationship("Vendor")
