"""Alert model — time-sensitive notifications."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin


class AlertSeverity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AlertStatus(str, enum.Enum):
    GENERATED = "generated"
    SEEN = "seen"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class Alert(Base, TimestampMixin, TenantOwned):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_account_property", 'account_id', 'property_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    alert_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id", ondelete="SET NULL"), nullable=True
    )
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity), default=AlertSeverity.MEDIUM)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), default=AlertStatus.GENERATED)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
