"""Financial report service — spending analysis, forecasting, and property comparison."""

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from mihomes.models.budget import Transaction
from mihomes.models.property import Property
from mihomes.models.vendor import Vendor
from mihomes.services.slug import resolve_identifier


def spending_by_vendor(
    session: Session,
    property_id_or_slug: str,
    start: date,
    end: date,
) -> list[dict]:
    """Get spending grouped by vendor for a property in a date range."""
    prop = resolve_identifier(session, Property, property_id_or_slug)
    rows = session.query(
        Transaction.vendor_id,
        Vendor.company_name,
        func.sum(Transaction.amount).label("total"),
        func.count(Transaction.id).label("count"),
    ).outerjoin(Vendor, Transaction.vendor_id == Vendor.id).filter(
        Transaction.property_id == prop.id,
        Transaction.date >= start,
        Transaction.date <= end,
    ).group_by(Transaction.vendor_id, Vendor.company_name).all()

    results = []
    for row in rows:
        results.append({
            "vendor": row.company_name or "Unassigned",
            "vendor_id": row.vendor_id,
            "total": round(row.total, 2),
            "transaction_count": row.count,
        })
    return sorted(results, key=lambda r: r["total"], reverse=True)


def spending_by_category(
    session: Session,
    property_id_or_slug: str,
    start: date,
    end: date,
) -> list[dict]:
    """Get spending grouped by category for a property in a date range."""
    prop = resolve_identifier(session, Property, property_id_or_slug)
    rows = session.query(
        Transaction.category,
        func.sum(Transaction.amount).label("total"),
        func.count(Transaction.id).label("count"),
    ).filter(
        Transaction.property_id == prop.id,
        Transaction.date >= start,
        Transaction.date <= end,
    ).group_by(Transaction.category).all()

    results = []
    for row in rows:
        results.append({
            "category": row.category,
            "total": round(row.total, 2),
            "transaction_count": row.count,
        })
    return sorted(results, key=lambda r: r["total"], reverse=True)


def forecast(
    session: Session,
    property_id_or_slug: str,
    months: int = 6,
) -> dict:
    """Forecast spending for a property based on historical average."""
    prop = resolve_identifier(session, Property, property_id_or_slug)
    # Look back 12 months for historical data
    end = date.today()
    start = date(end.year - 1, end.month, end.day)

    total_spending = session.query(
        func.sum(Transaction.amount),
    ).filter(
        Transaction.property_id == prop.id,
        Transaction.date >= start,
        Transaction.date <= end,
    ).scalar() or 0.0

    # Monthly average
    monthly_avg = total_spending / 12.0

    # Category breakdown
    category_rows = session.query(
        Transaction.category,
        func.sum(Transaction.amount).label("total"),
    ).filter(
        Transaction.property_id == prop.id,
        Transaction.date >= start,
        Transaction.date <= end,
    ).group_by(Transaction.category).all()

    category_forecast = []
    for row in category_rows:
        cat_monthly = row.total / 12.0
        category_forecast.append({
            "category": row.category,
            "monthly_avg": round(cat_monthly, 2),
            "forecast_total": round(cat_monthly * months, 2),
        })

    return {
        "property": prop.name,
        "property_slug": prop.slug,
        "historical_period": f"{start} to {end}",
        "monthly_average": round(monthly_avg, 2),
        "forecast_months": months,
        "forecast_total": round(monthly_avg * months, 2),
        "by_category": sorted(category_forecast, key=lambda r: r["forecast_total"], reverse=True),
    }


def property_comparison(
    session: Session,
    period_start: date,
    period_end: date,
) -> list[dict]:
    """Compare spending across all properties for a given period."""
    properties = session.query(Property).order_by(Property.name).all()
    results = []
    for prop in properties:
        total = session.query(
            func.sum(Transaction.amount),
        ).filter(
            Transaction.property_id == prop.id,
            Transaction.date >= period_start,
            Transaction.date <= period_end,
        ).scalar() or 0.0

        tx_count = session.query(
            func.count(Transaction.id),
        ).filter(
            Transaction.property_id == prop.id,
            Transaction.date >= period_start,
            Transaction.date <= period_end,
        ).scalar() or 0

        results.append({
            "property": prop.name,
            "property_slug": prop.slug,
            "total_spending": round(total, 2),
            "transaction_count": tx_count,
            "currency": prop.currency,
        })
    return sorted(results, key=lambda r: r["total_spending"], reverse=True)
