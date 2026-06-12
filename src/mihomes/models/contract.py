"""Contract model — vendor/service contracts."""

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, TimestampMixin


class Contract(Base, TimestampMixin):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=False)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    service_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    notice_period_days: Mapped[int] = mapped_column(Integer, default=30)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    vendor = relationship("Vendor")
    property = relationship("Property")


def _annualized_cost(self) -> float | None:
    """Total cost divided by contract duration in years.

    Returns cost as-is when no end date (open-ended/recurring contract).
    """
    if not self.cost:
        return None
    if not self.end_date or not self.start_date:
        return self.cost
    years = (self.end_date - self.start_date).days / 365.25
    if years <= 0:
        return self.cost
    return self.cost / years


Contract.annualized_cost = property(_annualized_cost)
