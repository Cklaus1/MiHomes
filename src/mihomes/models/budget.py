"""Budget and Transaction models."""

import enum
from datetime import date

from sqlalchemy import Date, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, TimestampMixin


class BudgetPeriod(str, enum.Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class Budget(Base, TimestampMixin):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    period: Mapped[BudgetPeriod] = mapped_column(Enum(BudgetPeriod), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD")

    property = relationship("Property")


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    vendor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    property = relationship("Property")
    vendor = relationship("Vendor")
