"""Work order model — maintenance and repair work tracking."""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin
from mihomes.type.money import Money


class WorkOrderStatus(str, enum.Enum):
    DRAFT = "draft"
    ESTIMATED = "estimated"
    APPROVED = "approved"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    VERIFIED = "verified"
    CANCELLED = "cancelled"


class WorkOrder(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "work_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Polymorphic: pairs with source_type, no ForeignKey. UUID because every id it
    # can hold is now a UUID (D2) — leaving it Integer produced
    # "CannotCoerce: cannot cast type uuid to integer" from create_work_order.
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vendors.id"), nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("staff.id"), nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Money, nullable=True)
    actual_cost: Mapped[float | None] = mapped_column(Money, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    status: Mapped[WorkOrderStatus] = mapped_column(Enum(WorkOrderStatus), default=WorkOrderStatus.DRAFT)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("issues.id"), index=True, nullable=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_report: Mapped[str | None] = mapped_column(Text, nullable=True)

    property = relationship("Property")
    vendor = relationship("Vendor")
    assignee = relationship("Staff")
    issue = relationship("Issue")
