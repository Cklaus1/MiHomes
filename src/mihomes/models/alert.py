"""Alert model — time-sensitive notifications."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.models import Base, TimestampMixin


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


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity), default=AlertSeverity.MEDIUM)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), default=AlertStatus.GENERATED)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
