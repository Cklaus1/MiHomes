"""Vendor service — CRUD operations."""

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from mihomes.models.property import Property
from mihomes.models.vendor import Vendor
from mihomes.models.vendor_rating import VendorRating
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.rating_validation import validate_scores
from mihomes.services.update_helpers import safe_update
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier

SERVICE_CATEGORIES = [
    "HVAC",
    "Plumbing",
    "Electrical",
    "Security",
    "Pest Control",
    "Landscaping",
    "Tree Service",
    "Pool",
    "Roofing",
    "Construction & Renovation",
    "Painting & Wood Repair",
    "Appliance Repair",
    "Carpet & Upholstery Cleaning",
    "Outdoor Lighting",
    "Irrigation & Water Systems",
    "Chimney",
    "Elevator",
    "Generator",
    "Masonry",
    "Golf Cart",
    "Auto Service",
    "Window & Gutter Cleaning",
    "Water Damage & Restoration",
    "Crawl Space & Sump Pump",
    "Gate & Door Systems",
    "Water Delivery",
    "Solar & Gas",
    "Internet & Cable",
    "Phone & AV Systems",
    "Dry Cleaning",
    "Piano Tuning",
    "Wine Cellar",
    "Locksmith",
    "Family Office",
]

_CATEGORY_MAP: dict[str, str] = {
    "tree cutting": "Tree Service",
    "tree cutting / stump removal": "Tree Service",
    "pool table": "Pool",
    "land lines": "Phone & AV Systems",
    "internet / cable": "Internet & Cable",
    "landscape lighting": "Outdoor Lighting",
    "outdoor-lighting": "Outdoor Lighting",
    "golf cart": "Golf Cart",
    "golf cart service": "Golf Cart",
    "golf cart sales": "Golf Cart",
    "golf cart key / locksmith": "Locksmith",
    "go cart repair": "Golf Cart",
    "dirt bikes": "Golf Cart",
    "wood-repair": "Painting & Wood Repair",
    "painting": "Painting & Wood Repair",
    "auto parts": "Auto Service",
    "auto service": "Auto Service",
    "subpumps": "Crawl Space & Sump Pump",
    "generator": "Generator",
    "electrical": "Electrical",
    "security-cameras": "Security",
    "security": "Security",
    "alarm": "Security",
    "pool": "Pool",
    "pest-control": "Pest Control",
    "exterminator": "Pest Control",
    "lake pump / tank controller": "Irrigation & Water Systems",
    "irrigation": "Irrigation & Water Systems",
    "hvac": "HVAC",
    "heating": "HVAC",
    "cooling": "HVAC",
    "bottled water": "Water Delivery",
    "water-delivery": "Water Delivery",
    "landscaping": "Landscaping",
    "plumbing": "Plumbing",
    "plumbing & filtration": "Plumbing",
    "filtration": "Plumbing",
    "appliance repair": "Appliance Repair",
    "appliance / refrigerator": "Appliance Repair",
    "water-damage": "Water Damage & Restoration",
    "restoration": "Water Damage & Restoration",
    "roofing": "Roofing",
    "family office": "Family Office",
    "carpet-cleaning": "Carpet & Upholstery Cleaning",
    "upholstery": "Carpet & Upholstery Cleaning",
    "carpet cleaning": "Carpet & Upholstery Cleaning",
    "designer rug cleaning": "Carpet & Upholstery Cleaning",
    "piano tuning": "Piano Tuning",
    "construction / renovation": "Construction & Renovation",
    "fencing": "Construction & Renovation",
    "construction": "Construction & Renovation",
    "gate operator": "Gate & Door Systems",
    "garage door": "Gate & Door Systems",
    "phone systems / gate / doorbells": "Phone & AV Systems",
    "chimney": "Chimney",
    "dry cleaning": "Dry Cleaning",
    "masonry": "Masonry",
    "attic ventilation / air sealing": "HVAC",
    "wine cellar repair": "Wine Cellar",
    "gas": "Solar & Gas",
    "solar": "Solar & Gas",
    "tesla": "Auto Service",
    "tesla service": "Auto Service",
    "elevator": "Elevator",
    "window-cleaning": "Window & Gutter Cleaning",
    "gutter-cleaning": "Window & Gutter Cleaning",
    "locksmith": "Locksmith",
}


def normalize_vendor_categories(session: Session) -> int:
    """Map all vendor service_categories to the canonical SERVICE_CATEGORIES list."""
    vendors = session.query(Vendor).filter(Vendor.service_categories.isnot(None)).all()
    count = 0
    for vendor in vendors:
        if not vendor.service_categories:
            continue
        seen: set[str] = set()
        normalized: list[str] = []
        for cat in vendor.service_categories:
            mapped = _CATEGORY_MAP.get(cat.strip().lower(), cat.strip())
            if mapped not in seen:
                seen.add(mapped)
                normalized.append(mapped)
        if normalized != vendor.service_categories:
            vendor.service_categories = normalized
            count += 1
    if count:
        session.flush()
    return count


def _resolve_properties(session: Session, property_ids: list[int]) -> list[Property]:
    """Resolve a list of property IDs to Property rows, ignoring unknown IDs."""
    if not property_ids:
        return []
    return session.query(Property).filter(Property.id.in_(property_ids)).all()


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
    )
    if property_ids:
        vendor.properties = _resolve_properties(session, property_ids)
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
    # property_ids is a read-only view over the vendor_properties link table;
    # route it through the relationship instead of the generic column setter.
    if "property_ids" in kwargs:
        pid_list = kwargs.pop("property_ids")
        vendor.properties = _resolve_properties(session, pid_list or [])
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
    # M5: shared 1–5 validation (cost/communication optional).
    validate_scores({
        "quality": (quality, True),
        "reliability": (reliability, True),
        "cost": (cost, False),
        "communication": (communication, False),
    })

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
    # H21/Q9: soft-delete. Vendors are referenced by contracts, transactions,
    # work orders and rating history via non-nullable FKs — a hard delete would
    # either violate those constraints or orphan the referencing rows. Instead
    # we flag the vendor inactive so it drops out of default lists while all
    # historical references stay intact.
    vendor = resolve_identifier(session, Vendor, id_or_slug)
    name = vendor.company_name
    if vendor.active:
        old_snap = snapshot_instance(vendor)
        vendor.active = False
        session.flush()
        changes = diff_instance(old_snap, snapshot_instance(vendor))
        record_change(session, "vendor", vendor.id, "delete", changes)
    return name
