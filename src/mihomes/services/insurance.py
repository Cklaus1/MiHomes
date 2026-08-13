"""Insurance service — policy tracking."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mihomes.models.insurance import InsurancePolicy, InsuranceType
from mihomes.models.property import Property
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.slug import get_by_id, resolve_identifier
from mihomes.services.update_helpers import safe_update


def create_policy(
    session: Session,
    carrier: str,
    insurance_type: InsuranceType,
    *,
    policy_number: str | None = None,
    agent_contact: str | None = None,
    coverage_limit: float | None = None,
    deductible: float | None = None,
    annual_premium: float | None = None,
    renewal_date: date | None = None,
    property_id_or_slug: str | None = None,
    currency: str = "USD",
    notes: str | None = None,
) -> InsurancePolicy:
    property_id = None
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        property_id = prop.id
    policy = InsurancePolicy(
        carrier=carrier, insurance_type=insurance_type, policy_number=policy_number,
        agent_contact=agent_contact, coverage_limit=coverage_limit,
        deductible=deductible, annual_premium=annual_premium,
        renewal_date=renewal_date, property_id=property_id,
        currency=currency, notes=notes,
    )
    session.add(policy)
    session.flush()
    record_change(session, "insurance", policy.id, "create", snapshot_instance(policy))
    return policy


def list_policies(
    session: Session,
    *,
    property_id_or_slug: str | None = None,
    expiring_days: int | None = None,
) -> list[InsurancePolicy]:
    query = session.query(InsurancePolicy)
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(InsurancePolicy.property_id == prop.id)
    if expiring_days:
        cutoff = date.today() + timedelta(days=expiring_days)
        # `!= None`, not `is not None`: SQLAlchemy overloads `!=` on a Column to emit
        # `IS NOT NULL`. See the same note in services/alerts.py.
        query = query.filter(
            InsurancePolicy.renewal_date != None,  # noqa: E711
            InsurancePolicy.renewal_date <= cutoff,
        )
    return query.order_by(InsurancePolicy.renewal_date.asc().nullslast()).all()


def update_policy(session: Session, policy_id: int, **kwargs) -> InsurancePolicy:
    policy = get_by_id(session, InsurancePolicy, policy_id)
    if policy is None:
        raise ValueError(f"Insurance policy {policy_id} not found")
    old_snap = snapshot_instance(policy)
    safe_update(policy, kwargs)
    session.flush()
    changes = diff_instance(old_snap, snapshot_instance(policy))
    if changes:
        record_change(session, "insurance", policy.id, "update", changes)
    return policy


def delete_policy(session: Session, policy_id: int) -> None:
    policy = get_by_id(session, InsurancePolicy, policy_id)
    if policy is None:
        raise ValueError(f"Insurance policy {policy_id} not found")
    record_change(session, "insurance", policy.id, "delete", snapshot_instance(policy))
    session.delete(policy)
    session.flush()
