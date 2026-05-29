"""Vendor service — CRUD operations."""

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from mihomes.models.vendor import Vendor
from mihomes.models.vendor_rating import VendorRating
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.update_helpers import safe_update
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier


def create_vendor(
    session: Session,
    company_name: str,
    *,
    contact_name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    service_categories: list[str] | None = None,
    service_areas: list[str] | None = None,
    insurance_info: str | None = None,
    notes: str | None = None,
    slug: str | None = None,
    property_ids: list[int] | None = None,
) -> Vendor:
    slug = ensure_unique_slug(session, Vendor, slug or generate_slug(company_name))
    vendor = Vendor(
        company_name=company_name,
        slug=slug,
        contact_name=contact_name,
        phone=phone,
        email=email,
        service_categories=service_categories,
        service_areas=service_areas,
        insurance_info=insurance_info,
        notes=notes,
        property_ids=property_ids,
    )
    session.add(vendor)
    session.flush()
    record_change(session, "vendor", vendor.id, "create", snapshot_instance(vendor))
    return vendor


def list_vendors(
    session: Session,
    *,
    category: str | None = None,
    active_only: bool = True,
) -> list[Vendor]:
    query = session.query(Vendor)
    if active_only:
        query = query.filter(Vendor.active.is_(True))
    vendors = query.order_by(Vendor.company_name).all()
    if category:
        vendors = [v for v in vendors if v.service_categories and category.lower() in [c.lower() for c in v.service_categories]]
    return vendors


def get_vendor(session: Session, id_or_slug: str) -> Vendor:
    return resolve_identifier(session, Vendor, id_or_slug)


def update_vendor(session: Session, id_or_slug: str, **kwargs) -> Vendor:
    vendor = resolve_identifier(session, Vendor, id_or_slug)
    old_snap = snapshot_instance(vendor)
    if "company_name" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(session, Vendor, generate_slug(kwargs["company_name"]), exclude_id=vendor.id)
    # Sync legacy fields from first contact when contacts list is provided
    if "contacts" in kwargs:
        if kwargs["contacts"]:
            first = kwargs["contacts"][0]
            kwargs["contact_name"] = first.get("name") or None
            kwargs["phone"] = first.get("phone") or None
            kwargs["email"] = first.get("email") or None
    safe_update(vendor, kwargs)
    session.flush()
    new_snap = snapshot_instance(vendor)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "vendor", vendor.id, "update", changes)
    return vendor


def rate_vendor(
    session: Session,
    id_or_slug: str,
    *,
    quality: int,
    reliability: int,
    cost: int | None = None,
    communication: int | None = None,
    notes: str | None = None,
    work_order_id: int | None = None,
    property_id: int | None = None,
) -> VendorRating:
    """Add a rating for a vendor. Scores are 1–5."""
    for name, val in [("quality", quality), ("reliability", reliability)]:
        if not 1 <= val <= 5:
            raise ValueError(f"{name} score must be between 1 and 5")
    for name, val in [("cost", cost), ("communication", communication)]:
        if val is not None and not 1 <= val <= 5:
            raise ValueError(f"{name} score must be between 1 and 5")

    vendor = resolve_identifier(session, Vendor, id_or_slug)
    scores = [quality, reliability]
    if cost is not None:
        scores.append(cost)
    if communication is not None:
        scores.append(communication)
    overall = round(sum(scores) / len(scores), 2)

    rating = VendorRating(
        vendor_id=vendor.id,
        quality_score=quality,
        reliability_score=reliability,
        cost_score=cost if cost is not None else quality,
        communication_score=communication if communication is not None else reliability,
        overall_score=overall,
        notes=notes,
        rated_date=date.today(),
        work_order_id=work_order_id,
        property_id=property_id,
    )
    session.add(rating)
    session.flush()
    return rating


def get_vendor_ratings(session: Session, id_or_slug: str) -> dict:
    """Return all ratings and aggregate averages for a vendor."""
    vendor = resolve_identifier(session, Vendor, id_or_slug)
    ratings = (
        session.query(VendorRating)
        .filter(VendorRating.vendor_id == vendor.id)
        .order_by(VendorRating.rated_date.desc())
        .all()
    )
    if not ratings:
        return {"vendor": vendor, "ratings": [], "averages": None}

    avg_quality = round(sum(r.quality_score for r in ratings) / len(ratings), 1)
    avg_reliability = round(sum(r.reliability_score for r in ratings) / len(ratings), 1)
    avg_cost = round(sum(r.cost_score for r in ratings) / len(ratings), 1)
    avg_communication = round(sum(r.communication_score for r in ratings) / len(ratings), 1)
    avg_overall = round(sum(r.overall_score for r in ratings) / len(ratings), 1)

    return {
        "vendor": vendor,
        "ratings": ratings,
        "averages": {
            "quality": avg_quality,
            "reliability": avg_reliability,
            "cost": avg_cost,
            "communication": avg_communication,
            "overall": avg_overall,
            "count": len(ratings),
        },
    }


def delete_category(session: Session, category: str) -> int:
    """Remove a service category string from every vendor that has it."""
    vendors = session.query(Vendor).filter(Vendor.service_categories.isnot(None)).all()
    count = 0
    for vendor in vendors:
        if vendor.service_categories and category in vendor.service_categories:
            vendor.service_categories = [c for c in vendor.service_categories if c != category]
            count += 1
    if count:
        session.flush()
    return count


def rename_category(session: Session, old_name: str, new_name: str) -> int:
    """Rename a service category string across every vendor that has it."""
    if not new_name:
        return 0
    vendors = session.query(Vendor).filter(Vendor.service_categories.isnot(None)).all()
    count = 0
    for vendor in vendors:
        if vendor.service_categories and old_name in vendor.service_categories:
            vendor.service_categories = [new_name if c == old_name else c for c in vendor.service_categories]
            count += 1
    if count:
        session.flush()
    return count


def delete_vendor(session: Session, id_or_slug: str) -> str:
    vendor = resolve_identifier(session, Vendor, id_or_slug)
    name = vendor.company_name
    record_change(session, "vendor", vendor.id, "delete", snapshot_instance(vendor))
    session.delete(vendor)
    session.flush()
    return name
