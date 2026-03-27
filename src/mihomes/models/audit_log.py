"""Audit log model — immutable record of all changes."""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.models import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # create, update, delete
    changes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(100), default="admin")
