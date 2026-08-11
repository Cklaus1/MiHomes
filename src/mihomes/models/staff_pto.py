"""Staff PTO request model."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin


class PTOStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class StaffPTORequest(Base, TimestampMixin, TenantOwned):
    __tablename__ = "staff_pto_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("staff.id"), nullable=False)
    dates: Mapped[list] = mapped_column(JSON, nullable=False)  # list of "YYYY-MM-DD" strings
    status: Mapped[PTOStatus] = mapped_column(Enum(PTOStatus), default=PTOStatus.PENDING)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    coverage_warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)

    staff = relationship("Staff")
