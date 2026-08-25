"""Vendor rating service — rate vendors and compare scores.

**Every function here is gated on the `vendor_ratings` entitlement (D12), and all three currently
have zero callers** (F6). Gating dead code is deliberate rather than thorough-for-its-own-sake:
the live path is `services/vendor.py::get_vendor_ratings`, and whoever eventually wires *these* up
— the write path §3.2 assumes exists, or a comparison screen — inherits the gate instead of
reopening the hole. A module left ungated because nothing calls it today is a paywall with a
scheduled expiry date.

Unlike the read path, these **raise** rather than returning empty. N11's "the page must still
load" is about a panel among many on a dashboard; a caller explicitly asking to *create* a rating
or *compare* vendors has no partial answer to render, and silently doing nothing would be worse
than a clear refusal.
"""

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


def _require_ratings_entitlement(session: Session) -> None:
    """Raise unless the bound account's plan includes vendor ratings (D12/D14)."""
    from mihomes.entitlements.limits import UPGRADE_PATH
    from mihomes.entitlements.service import Denied, can
    from mihomes.models.account import Account
    from mihomes.services.property import EntitlementError
    from mihomes.tenancy import current_account

    account_id = current_account.get(None)
    if account_id is None:
        return  # operator CLI / background job — no household to bill (SPEC-002 D1)

    account = session.get(Account, account_id)
    if account is None:  # pragma: no cover - a bound account always exists
        return

    decision = can(account, "vendor.rate")
    if isinstance(decision, Denied):
        raise EntitlementError(
            Denied(
                reason=(
                    f"Vendor ratings are not included in the {account.plan} plan."
                ),
                upgrade_target=UPGRADE_PATH.get(getattr(account, "plan", "free")),
                limit=False,
            )
        )


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
    _require_ratings_entitlement(session)
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
    _require_ratings_entitlement(session)
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
    _require_ratings_entitlement(session)
    results = []
    for slug in vendor_slugs:
        scores = get_vendor_scores(session, slug)
        results.append(scores)
    return sorted(results, key=lambda r: r["overall"] or 0, reverse=True)
