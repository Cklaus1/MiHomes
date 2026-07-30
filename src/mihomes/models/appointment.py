"""Appointment model — vendor visits, inspections, deliveries, and other scheduled events."""

from datetime import date, time
from enum import Enum

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, TimestampMixin


class AppointmentType(str, Enum):
    VENDOR_VISIT = "vendor_visit"
    INSPECTION = "inspection"
    DELIVERY = "delivery"
    MAINTENANCE = "maintenance"
    OTHER = "other"


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    vendor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=True)
    contract_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("contracts.id"), nullable=True)
    recurring_expense_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("recurring_expenses.id"), index=True, nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    appointment_type: Mapped[str] = mapped_column(String(50), default="vendor_visit", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    gcal_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    property = relationship("Property")
    vendor = relationship("Vendor")
    contract = relationship("Contract")
    recurring_expense = relationship("RecurringExpense")
