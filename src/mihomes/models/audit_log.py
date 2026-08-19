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
    # No default. `"admin"` was a fiction every call site inherited (SPEC-003 F6): the column
    # recorded a principal that had not acted. `services.audit.record_change` and
    # `authz.audit.audit_write` both set it explicitly — from `current_user` where there is one,
    # and from the honest label `"system"` where there is not. Removing the default means a new
    # call site that forgets fails closed (NOT NULL) instead of writing a plausible lie.
    #
    # Python-side default only, so removing it is not a schema change: `autogenerate` stays empty.
    actor: Mapped[str] = mapped_column(String(100))
