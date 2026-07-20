"""Contract service — vendor contract management."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mihomes.models.contract import Contract
from mihomes.models.property import Property
from mihomes.models.vendor import Vendor
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.slug import resolve_identifier
from mihomes.services.update_helpers import safe_update


def create_contract(
    session: Session,
    vendor_id_or_slug: str,
    property_id_or_slug: str,
    start_date: date,
    *,
    end_date: date | None = None,
    service_category: str | None = None,
    auto_renew: bool = False,
    notice_period_days: int = 30,
    billing_frequency: str | None = None,
    cost: float | None = None,
    currency: str = "USD",
    notes: str | None = None,
) -> Contract:
    vendor = resolve_identifier(session, Vendor, vendor_id_or_slug)
    prop = resolve_identifier(session, Property, property_id_or_slug)
    contract = Contract(
        vendor_id=vendor.id, property_id=prop.id, start_date=start_date,
        end_date=end_date, service_category=service_category,
        auto_renew=auto_renew, notice_period_days=notice_period_days,
        billing_frequency=billing_frequency, cost=cost, currency=currency, notes=notes,
    )
    session.add(contract)
    session.flush()
    record_change(session, "contract", contract.id, "create", snapshot_instance(contract))
    return contract


def list_contracts(
    session: Session,
    *,
    property_id_or_slug: str | None = None,
    expiring_days: int | None = None,
) -> list[Contract]:
    query = session.query(Contract)
    if property_id_or_slug:
        prop = resolve_identifier(session, Property, property_id_or_slug)
        query = query.filter(Contract.property_id == prop.id)
    if expiring_days:
        cutoff = date.today() + timedelta(days=expiring_days)
        query = query.filter(Contract.end_date.is_not(None), Contract.end_date <= cutoff)
    return query.order_by(Contract.end_date.asc().nullslast()).all()


def update_contract(session: Session, contract_id: int, **kwargs) -> Contract:
    contract = session.get(Contract, contract_id)
    if contract is None:
        raise ValueError(f"Contract {contract_id} not found")
    old_snap = snapshot_instance(contract)
    safe_update(contract, kwargs)
    session.flush()
    changes = diff_instance(old_snap, snapshot_instance(contract))
    if changes:
        record_change(session, "contract", contract.id, "update", changes)
    return contract


def delete_contract(session: Session, contract_id: int) -> None:
    contract = session.get(Contract, contract_id)
    if contract is None:
        raise ValueError(f"Contract {contract_id} not found")
    record_change(session, "contract", contract.id, "delete", snapshot_instance(contract))
    session.delete(contract)
    session.flush()
