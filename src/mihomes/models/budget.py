"""Budget and Transaction models."""

import enum
from datetime import date

from sqlalchemy import Date, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, TimestampMixin


class BudgetPeriod(str, enum.Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class Budget(Base, TimestampMixin):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint(
            "property_id", "category", "period", "period_start",
            name="uq_budget_property_category_period",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), index=True, nullable=False)
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
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), index=True, nullable=False)
    vendor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vendors.id"), index=True, nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    work_order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("work_orders.id", ondelete="SET NULL"), index=True, nullable=True
    )
    appointment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="SET NULL"), index=True, nullable=True
    )

    property = relationship("Property")
    vendor = relationship("Vendor")
    work_order = relationship(
        "WorkOrder",
        primaryjoin="Transaction.work_order_id == WorkOrder.id",
        foreign_keys="[Transaction.work_order_id]",
        viewonly=True,
    )
    appointment = relationship(
        "Appointment",
        primaryjoin="Transaction.appointment_id == Appointment.id",
        foreign_keys="[Transaction.appointment_id]",
        viewonly=True,
    )
