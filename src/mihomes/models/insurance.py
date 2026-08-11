"""Insurance policy model."""

import enum
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    carrier: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    insurance_type: Mapped[InsuranceType] = mapped_column(Enum(InsuranceType), nullable=False)
    coverage_limit: Mapped[float | None] = mapped_column(Money, nullable=True)
    deductible: Mapped[float | None] = mapped_column(Money, nullable=True)
    annual_premium: Mapped[float | None] = mapped_column(Money, nullable=True)
    renewal_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    property_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("properties.id"), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    property = relationship("Property")
