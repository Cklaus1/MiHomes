"""Budget and Transaction models."""

import enum
import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin
from mihomes.type.money import Money


class BudgetPeriod(str, enum.Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class Budget(Base, TimestampMixin, TenantOwned):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint(
            "property_id", "category", "period", "period_start",
            name="uq_budget_property_category_period",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    period: Mapped[BudgetPeriod] = mapped_column(Enum(BudgetPeriod), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[float] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD")

    # NOTE: defined before the `property` relationship below, which otherwise
    # shadows the built-in `property` decorator inside the class body.
    @property
    def period_end(self) -> date:
        """Exclusive end of this budget's period window (H16).

        Returns the date one period after ``period_start`` so callers can use a
        half-open ``[period_start, period_end)`` range. Day-of-month is
        preserved and clamped to the last valid day of the target month (so a
        Jan-31 monthly budget ends Feb-28/29). Month arithmetic is done by hand
        to avoid a dateutil dependency.
        """
        import calendar

        months = {
            BudgetPeriod.MONTHLY: 1,
            BudgetPeriod.QUARTERLY: 3,
            BudgetPeriod.ANNUAL: 12,
        }[self.period]
        total = (self.period_start.month - 1) + months
        year = self.period_start.year + total // 12
        month = total % 12 + 1
        day = min(self.period_start.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    property = relationship("Property")


class Transaction(Base, TimestampMixin, TenantOwned):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    amount: Mapped[float] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), index=True, nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vendors.id"), index=True, nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("work_orders.id", ondelete="SET NULL"), index=True, nullable=True
    )
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("appointments.id", ondelete="SET NULL"), index=True, nullable=True
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
