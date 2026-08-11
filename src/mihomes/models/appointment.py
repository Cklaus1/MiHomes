"""Appointment model — vendor visits, inspections, deliveries, and other scheduled events."""

import uuid
from datetime import date, time
from enum import Enum

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, Time
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin


class AppointmentType(str, Enum):
    VENDOR_VISIT = "vendor_visit"
    INSPECTION = "inspection"
    DELIVERY = "delivery"
    MAINTENANCE = "maintenance"
    OTHER = "other"


class Appointment(Base, TimestampMixin, TenantOwned):
    __tablename__ = "appointments"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vendors.id"), nullable=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contracts.id"), nullable=True)
    recurring_expense_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("recurring_expenses.id"), index=True, nullable=True)
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
