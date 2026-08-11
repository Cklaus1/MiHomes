"""Staff PTO request model."""

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, TenantOwned, TimestampMixin


class PTOStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class StaffPTORequest(Base, TimestampMixin, TenantOwned):
    __tablename__ = "staff_pto_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(Integer, ForeignKey("staff.id"), nullable=False)
    dates: Mapped[list] = mapped_column(JSON, nullable=False)  # list of "YYYY-MM-DD" strings
    status: Mapped[PTOStatus] = mapped_column(Enum(PTOStatus), default=PTOStatus.PENDING)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    coverage_warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)

    staff = relationship("Staff")
