"""Issue model — problems discovered at properties."""

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    space_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("spaces.id"), nullable=True)
    severity: Mapped[IssueSeverity] = mapped_column(Enum(IssueSeverity), default=IssueSeverity.MEDIUM)
    status: Mapped[IssueStatus] = mapped_column(Enum(IssueStatus), default=IssueStatus.REPORTED)
    photos: Mapped[list | None] = mapped_column(JSON, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_by_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("staff.id"), index=True, nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("staff.id"), index=True, nullable=True)

    property = relationship("Property")
    space = relationship("Space")
    reported_by = relationship("Staff", foreign_keys=[reported_by_id])
    resolved_by = relationship("Staff", foreign_keys=[resolved_by_id])
