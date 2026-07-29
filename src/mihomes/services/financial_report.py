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


def _history_months(session: Session, property_id: int, start: date, end: date) -> float:
    """Number of months of transaction history actually on record (M4).

    Spans from the earliest transaction in the look-back window to `end`, so a
    property with only three months of data averages over three months, not
    twelve. Clamped to [1, 12]: never divide by zero, never claim more history
    than the one-year window holds.
    """
    earliest = session.query(func.min(Transaction.date)).filter(
        Transaction.property_id == property_id,
        Transaction.date >= start,
        Transaction.date <= end,
    ).scalar()
    if earliest is None:
        return 1.0
    span = (end.year - earliest.year) * 12 + (end.month - earliest.month) + 1
    return float(max(1, min(12, span)))


def forecast(
    session: Session,
    property_id_or_slug: str,
    months: int = 6,
) -> dict:
    """Forecast spending for a property based on historical average."""
    prop = resolve_identifier(session, Property, property_id_or_slug)
    # Look back 12 months for historical data. Subtract 365 days rather than
    # constructing date(year-1, month, day) — the latter crashes on Feb 29 in a
    # non-leap prior year (M4).
    end = date.today()
    start = end - timedelta(days=365)

    total_spending = session.query(
        func.sum(Transaction.amount),
    ).filter(
        Transaction.property_id == prop.id,
        Transaction.date >= start,
        Transaction.date <= end,
    ).scalar() or 0.0

    # Divide by the actual months of history available, not a blind 12 (M4).
    # A property with 3 months of data should forecast off a 3-month average,
    # otherwise the monthly figure is diluted 4x and every forecast reads low.
    divisor = _history_months(session, prop.id, start, end)
    monthly_avg = total_spending / divisor

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
        cat_monthly = row.total / divisor
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


def vendor_spending_report(
    session: Session,
    start: date,
    end: date,
    property_id_or_slug: str | None = None,
) -> list[dict]:
    """Combined vendor spending from transactions + work order actual costs."""
    from mihomes.models.work_order import WorkOrder
    from sqlalchemy import case

    prop_id = None
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        prop_id = prop.id

    # --- Transactions with vendor_id ---
    tx_q = session.query(
        Transaction.vendor_id,
        Vendor.company_name,
        func.sum(Transaction.amount).label("tx_total"),
        func.count(Transaction.id).label("tx_count"),
    ).outerjoin(Vendor, Transaction.vendor_id == Vendor.id).filter(
        Transaction.vendor_id.isnot(None),
        Transaction.date >= start,
        Transaction.date <= end,
    )
    if prop_id:
        tx_q = tx_q.filter(Transaction.property_id == prop_id)
    tx_rows = tx_q.group_by(Transaction.vendor_id, Vendor.company_name).all()

    # --- Work orders with actual_cost ---
    wo_q = session.query(
        WorkOrder.vendor_id,
        Vendor.company_name,
        func.sum(WorkOrder.actual_cost).label("wo_total"),
        func.count(WorkOrder.id).label("wo_count"),
    ).join(Vendor, WorkOrder.vendor_id == Vendor.id).filter(
        WorkOrder.vendor_id.isnot(None),
        WorkOrder.actual_cost.isnot(None),
        WorkOrder.updated_at >= start,
        WorkOrder.updated_at <= end,
    )
    if prop_id:
        wo_q = wo_q.filter(WorkOrder.property_id == prop_id)
    wo_rows = wo_q.group_by(WorkOrder.vendor_id, Vendor.company_name).all()

    # Merge by vendor_id
    merged: dict[int, dict] = {}
    for r in tx_rows:
        merged[r.vendor_id] = {
            "vendor_id": r.vendor_id,
            "vendor": r.company_name or "Unknown",
            "tx_total": round(r.tx_total or 0, 2),
            "tx_count": r.tx_count or 0,
            "wo_total": 0.0,
            "wo_count": 0,
        }
    for r in wo_rows:
        if r.vendor_id in merged:
            merged[r.vendor_id]["wo_total"] = round(r.wo_total or 0, 2)
            merged[r.vendor_id]["wo_count"] = r.wo_count or 0
        else:
            merged[r.vendor_id] = {
                "vendor_id": r.vendor_id,
                "vendor": r.company_name or "Unknown",
                "tx_total": 0.0,
                "tx_count": 0,
                "wo_total": round(r.wo_total or 0, 2),
                "wo_count": r.wo_count or 0,
            }

    results = []
    for v in merged.values():
        v["combined_total"] = round(v["tx_total"] + v["wo_total"], 2)
        results.append(v)

    return sorted(results, key=lambda r: r["combined_total"], reverse=True)


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
