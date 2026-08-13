"""Issue model — problems discovered at properties."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin


class IssueSeverity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueStatus(str, enum.Enum):
    REPORTED = "reported"
    ASSESSED = "assessed"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in-progress"
    RESOLVED = "resolved"
    VERIFIED = "verified"


class Issue(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "issues"
    __table_args__ = (
        Index("ix_issues_account_reported_by", 'account_id', 'reported_by_id'),
        Index("ix_issues_account_resolved_by", 'account_id', 'resolved_by_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    space_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("spaces.id"), nullable=True)
    severity: Mapped[IssueSeverity] = mapped_column(Enum(IssueSeverity), default=IssueSeverity.MEDIUM)
    status: Mapped[IssueStatus] = mapped_column(Enum(IssueStatus), default=IssueStatus.REPORTED)
    photos: Mapped[list | None] = mapped_column(JSON, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("staff.id"), nullable=True)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("staff.id"), nullable=True)

    property = relationship("Property")
    space = relationship("Space")
    reported_by = relationship("Staff", foreign_keys=[reported_by_id])
    resolved_by = relationship("Staff", foreign_keys=[resolved_by_id])
