"""Audit log model — immutable record of all changes."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned


class AuditLog(Base, TenantOwned):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_account_ts", 'account_id', 'timestamp'),
        Index("ix_audit_log_account_entity", 'account_id', 'entity_type', 'entity_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # create, update, delete
    changes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(100), default="admin")
