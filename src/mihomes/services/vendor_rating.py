"""Vendor rating service — rate vendors and compare scores."""

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from mihomes.models.property import Property
from mihomes.models.vendor import Vendor
from mihomes.models.vendor_rating import VendorRating
from mihomes.models.work_order import WorkOrder
from mihomes.services.audit import record_change, snapshot_instance
from mihomes.services.rating_validation import validate_scores
from mihomes.services.slug import resolve_identifier


def create_rating(
    session: Session,
    vendor_id_or_slug: str,
    quality_score: int,
    reliability_score: int,
    cost_score: int,
    communication_score: int,
    *,
    work_order_id_or_slug: str | None = None,
    property_id_or_slug: str | None = None,
    notes: str | None = None,
    rated_date: date | None = None,
) -> VendorRating:
    # M5: enforce the same 1–5 bounds as vendor.rate_vendor via the shared helper.
    validate_scores({
        "quality": (quality_score, True),
        "reliability": (reliability_score, True),
        "cost": (cost_score, True),
        "communication": (communication_score, True),
    })
    vendor = resolve_identifier(session, Vendor, vendor_id_or_slug)
    wo_id = None
    if work_order_id_or_slug:
        wo = resolve_identifier(session, WorkOrder, work_order_id_or_slug)
        wo_id = wo.id
    prop_id = None
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        prop_id = prop.id
    overall = (quality_score + reliability_score + cost_score + communication_score) / 4.0
    rating = VendorRating(
        vendor_id=vendor.id, work_order_id=wo_id, property_id=prop_id,
        quality_score=quality_score, reliability_score=reliability_score,
        cost_score=cost_score, communication_score=communication_score,
        overall_score=round(overall, 2), notes=notes,
        rated_date=rated_date or date.today(),
    )
    session.add(rating)
    session.flush()
    record_change(session, "vendor_rating", rating.id, "create", snapshot_instance(rating))
    return rating


def get_vendor_scores(session: Session, vendor_id_or_slug: str) -> dict:
    """Get average scores for a vendor."""
    vendor = resolve_identifier(session, Vendor, vendor_id_or_slug)
    result = session.query(
        func.avg(VendorRating.quality_score).label("quality"),
        func.avg(VendorRating.reliability_score).label("reliability"),
        func.avg(VendorRating.cost_score).label("cost"),
        func.avg(VendorRating.communication_score).label("communication"),
        func.avg(VendorRating.overall_score).label("overall"),
        func.count(VendorRating.id).label("count"),
    ).filter(VendorRating.vendor_id == vendor.id).first()
    return {
        "vendor": vendor.company_name,
        "vendor_slug": vendor.slug,
        "quality": round(result.quality, 2) if result.quality else None,
        "reliability": round(result.reliability, 2) if result.reliability else None,
        "cost": round(result.cost, 2) if result.cost else None,
        "communication": round(result.communication, 2) if result.communication else None,
        "overall": round(result.overall, 2) if result.overall else None,
        "rating_count": result.count,
    }


def compare_vendors(session: Session, vendor_slugs: list[str]) -> list[dict]:
    """Compare multiple vendors by their average scores."""
    results = []
    for slug in vendor_slugs:
        scores = get_vendor_scores(session, slug)
        results.append(scores)
    return sorted(results, key=lambda r: r["overall"] or 0, reverse=True)
