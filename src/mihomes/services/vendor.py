"""Vendor service — CRUD operations."""

from sqlalchemy.orm import Session

from mihomes.models.vendor import Vendor
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
    safe_update(vendor, kwargs)
    session.flush()
    new_snap = snapshot_instance(vendor)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "vendor", vendor.id, "update", changes)
    return vendor


def delete_vendor(session: Session, id_or_slug: str) -> str:
    vendor = resolve_identifier(session, Vendor, id_or_slug)
    name = vendor.company_name
    record_change(session, "vendor", vendor.id, "delete", snapshot_instance(vendor))
    session.delete(vendor)
    session.flush()
    return name
