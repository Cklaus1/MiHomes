"""Insurance policy model."""

import enum
import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned, TimestampMixin
from mihomes.type.money import Money


class InsuranceType(str, enum.Enum):
    HOMEOWNERS = "homeowners"
    LIABILITY = "liability"
    UMBRELLA = "umbrella"
    VALUABLE_ARTICLES = "valuable-articles"
    VEHICLE = "vehicle"
    WORKERS_COMP = "workers-comp"
    EVENT = "event"
    OTHER = "other"


class InsurancePolicy(Base, TimestampMixin, TenantOwned):
    __tablename__ = "insurance_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    policy_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    carrier: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    insurance_type: Mapped[InsuranceType] = mapped_column(Enum(InsuranceType), nullable=False)
    coverage_limit: Mapped[float | None] = mapped_column(Money, nullable=True)
    deductible: Mapped[float | None] = mapped_column(Money, nullable=True)
    annual_premium: Mapped[float | None] = mapped_column(Money, nullable=True)
    renewal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    property = relationship("Property")
