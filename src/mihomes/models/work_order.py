"""Work order model — maintenance and repair work tracking."""

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vendor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    assignee_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("staff.id"), nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Money, nullable=True)
    actual_cost: Mapped[float | None] = mapped_column(Money, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    status: Mapped[WorkOrderStatus] = mapped_column(Enum(WorkOrderStatus), default=WorkOrderStatus.DRAFT)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    issue_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("issues.id"), index=True, nullable=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_report: Mapped[str | None] = mapped_column(Text, nullable=True)

    property = relationship("Property")
    vendor = relationship("Vendor")
    assignee = relationship("Staff")
    issue = relationship("Issue")
