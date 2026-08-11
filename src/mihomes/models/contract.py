"""Contract model — vendor/service contracts."""

from datetime import date
from enum import Enum

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, TenantOwned, TimestampMixin
from mihomes.type.money import Money


class BillingFrequency(str, Enum):
    MONTHLY = "monthly"
    BIMONTHLY = "bimonthly"
    QUARTERLY = "quarterly"
    SEMI_ANNUAL = "semi-annual"
    ANNUAL = "annual"
    ONE_TIME = "one-time"


_PERIODS_PER_YEAR: dict[str, float] = {
    "monthly": 12,
    "bimonthly": 6,
    "quarterly": 4,
    "semi-annual": 2,
    "annual": 1,
}


class Contract(Base, TimestampMixin, TenantOwned):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=False)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    service_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    notice_period_days: Mapped[int] = mapped_column(Integer, default=30)
    billing_frequency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cost: Mapped[float | None] = mapped_column(Money, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    vendor = relationship("Vendor")
    property = relationship("Property")


def _annualized_cost(self) -> float | None:
    """Cost × periods/year for recurring; total ÷ years for one-time; cost as-is if no frequency or dates."""
    if not self.cost:
        return None
    freq = self.billing_frequency
    if freq and freq in _PERIODS_PER_YEAR:
        return self.cost * _PERIODS_PER_YEAR[freq]
    # one-time or legacy (no frequency) — use date range
    if not self.end_date or not self.start_date:
        return self.cost
    years = (self.end_date - self.start_date).days / 365.25
    if years <= 0:
        return self.cost
    return self.cost / years


def _cost_display(self) -> str | None:
    """Human-readable cost string, e.g. '$500 / month' or '$90,000 total'."""
    if not self.cost:
        return None
    freq = self.billing_frequency
    label = {
        "monthly": "/ month",
        "bimonthly": "/ 2 months",
        "quarterly": "/ quarter",
        "semi-annual": "/ 6 months",
        "annual": "/ year",
        "one-time": "total",
    }.get(freq or "", "")
    return f"${self.cost:,.2f} {label}".strip()


Contract.annualized_cost = property(_annualized_cost)
Contract.cost_display = property(_cost_display)
