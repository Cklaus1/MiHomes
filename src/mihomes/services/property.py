"""Property service — CRUD operations with audit logging."""

from datetime import date

from sqlalchemy.orm import Session

from mihomes.models.property import Property, PropertyStatus, PropertyType
from mihomes.services.audit import diff_instance, record_change, snapshot_instance
from mihomes.services.slug import ensure_unique_slug, generate_slug, resolve_identifier
from mihomes.services.update_helpers import safe_update
from mihomes.services.validators import validate_name


def _check_home_entitlement(session: Session) -> None:
    """`can("property.add")` — server-side, inside the caller's transaction (`PRICING` rule 5).

    **In Phase 2 this never denies**, and that is correct rather than pointless: D7 makes every
    account `free` and D18 defers the gates, so the limits table says "free, unlimited" (§7's
    deferred table). What the call buys now is the *call site* — `SAAS_PRD:144`: shipping the
    service in Phase 2 is what *"prevents Phase 2 secretly depending on Phase 3."* Phase 3 swaps
    which table is active and this gate comes alive without touching this file.

    The count is taken here, in the same transaction as the insert, because rule 5 requires it:
    counting outside the transaction reintroduces the race the check exists to prevent.
    """
    from sqlalchemy import func, select

    from mihomes.entitlements import Denied, can
    from mihomes.models.account import Account
    from mihomes.tenancy import require_account

    account_id = require_account()
    account = session.get(Account, account_id)
    if account is None:  # pragma: no cover - a bound account always exists
        return

    current = session.execute(
        select(func.count()).select_from(Property).where(Property.account_id == account_id)
    ).scalar_one()

    decision = can(account, "property.add", {"current_homes": current})
    if isinstance(decision, Denied):
        # **The gate refuses; it does not start a trial.** It used to (`maybe_start_trial` here),
        # which meant a Free user adding a 2nd home was silently flipped to Pro-on-trial with
        # nothing on screen — the cap looked broken. §4.1 asks for a *choice*: "Adding another
        # home is a Pro feature. Start your 14-day Pro trial." The trial now starts only when the
        # user presses that button (`POST /billing/trial`), still at the moment of intent §4.2
        # wants, but with consent.
        from mihomes.services.billing.trial import trial_available

        raise EntitlementError(decision, trial_available=trial_available(account))


def home_upgrade_prompt(session: Session) -> dict | None:
    """Would adding a home be refused right now? — the read-only twin of the gate above.

    `None` when the add is allowed (or there is no bound account: CLI operator). Otherwise the
    facts the upgrade popup needs: the service's reason, the plan that would allow it, and
    whether this account can still start its one trial.

    **No writes, and asked only by the routes that render the Add button** — the same shape as
    `ratings_are_entitled`, deliberately unlike the reverted trial banner (6628d39), which read
    the account on every request and hung the app when that read failed.
    """
    from sqlalchemy import func, select

    from mihomes.entitlements import Denied, can
    from mihomes.models.account import Account
    from mihomes.services.billing.trial import trial_available
    from mihomes.tenancy import current_account

    account_id = current_account.get(None)
    if account_id is None:
        return None
    account = session.get(Account, account_id)
    if account is None:  # pragma: no cover - a bound account always exists
        return None

    current = session.execute(
        select(func.count()).select_from(Property).where(Property.account_id == account_id)
    ).scalar_one()
    decision = can(account, "property.add", {"current_homes": current})
    if not isinstance(decision, Denied):
        return None
    return {
        "reason": decision.reason,
        "upgrade_target": decision.upgrade_target,
        "limit": decision.limit,
        "trial_available": trial_available(account),
    }


def _refuse_if_frozen(session: Session, prop: Property) -> None:
    """Refuse a write to a frozen home — `PRICING` §4.3, A20.

    *"Frozen homes are read-only, never deleted: view and export yes; create/edit/complete/
    AI-advise no."* This is the **edit** half; the read half is that nothing here touches queries,
    so a frozen home stays fully visible and exportable.

    Raises `EntitlementError` with the upgrade target, so the UI renders rule 4's prompt rather
    than a dead end: the customer is one payment away from editing their own house, and the error
    should say so.
    """
    from mihomes.entitlements.limits import UPGRADE_PATH
    from mihomes.entitlements.service import Denied
    from mihomes.models.account import Account
    from mihomes.services.billing.restricted import restriction_for
    from mihomes.tenancy import require_account

    account = session.get(Account, require_account())
    if account is None:  # pragma: no cover - a bound account always exists
        return

    restriction = restriction_for(session, account)
    if restriction.may_edit(prop.id):
        return

    raise EntitlementError(
        Denied(
            reason=(
                f"{prop.name} is read-only: the {account.plan} plan covers "
                f"{restriction.max_homes} home(s) and this account has "
                f"{len(restriction.active_ids) + len(restriction.frozen_ids)}. "
                "Nothing has been deleted — upgrade to edit it again."
            ),
            upgrade_target=UPGRADE_PATH.get(getattr(account, "plan", "free")),
            limit=restriction.max_homes,
        )
    )


class EntitlementError(Exception):
    """A plan limit refused the action. Carries the `Denied` so the UI can render rule 4's
    upgrade prompt rather than a bare error string."""

    def __init__(self, decision, *, trial_available: bool = False):
        from mihomes.authz.scope import current_role

        self.decision = decision
        # Both captured at the raise site. The exception handler runs after the request's
        # transaction has rolled back *and* after its authz context has been reset, so it can
        # neither read the account nor see the role — it would call every owner a non-owner.
        self.trial_available = trial_available
        self.is_owner = current_role.get(None) == "owner"
        super().__init__(decision.reason)


def create_property(
    session: Session,
    name: str,
    *,
    address: str | None = None,
    property_type: PropertyType = PropertyType.OTHER,
    status: PropertyStatus = PropertyStatus.OPEN,
    climate_zone: str | None = None,
    sqft: int | None = None,
    features: str | None = None,
    currency: str = "USD",
    slug: str | None = None,
) -> Property:
    name = validate_name(name, "property")
    _check_home_entitlement(session)
    slug = ensure_unique_slug(session, Property, slug or generate_slug(name))
    prop = Property(
        name=name,
        slug=slug,
        address=address,
        property_type=property_type,
        status=status,
        climate_zone=climate_zone,
        sqft=sqft,
        features=features,
        currency=currency,
    )
    session.add(prop)
    session.flush()
    record_change(session, "property", prop.id, "create", snapshot_instance(prop))
    return prop


def list_properties(
    session: Session,
    *,
    status: PropertyStatus | None = None,
    property_type: PropertyType | None = None,
) -> list[Property]:
    query = session.query(Property)
    if status is not None:
        query = query.filter(Property.status == status)
    if property_type is not None:
        query = query.filter(Property.property_type == property_type)
    return query.order_by(Property.name).all()


def get_property(session: Session, id_or_slug: str) -> Property:
    return resolve_identifier(session, Property, id_or_slug)


def update_property(session: Session, id_or_slug: str, **kwargs) -> Property:
    prop = resolve_identifier(session, Property, id_or_slug)
    _refuse_if_frozen(session, prop)
    old_snap = snapshot_instance(prop)

    # Handle slug change if name changes
    if "name" in kwargs and "slug" not in kwargs:
        kwargs["slug"] = ensure_unique_slug(
            session, Property, generate_slug(kwargs["name"]), exclude_id=prop.id
        )

    safe_update(prop, kwargs)

    session.flush()
    new_snap = snapshot_instance(prop)
    changes = diff_instance(old_snap, new_snap)
    if changes:
        record_change(session, "property", prop.id, "update", changes)
    return prop


def delete_property(session: Session, id_or_slug: str) -> str:
    from mihomes.models.asset import Asset
    from mihomes.models.budget import Budget, Transaction
    from mihomes.models.issue import Issue
    from mihomes.models.task import Task

    prop = resolve_identifier(session, Property, id_or_slug)
    name = prop.name

    # Check for dependent records and delete them (cascade)
    session.query(Transaction).filter(Transaction.property_id == prop.id).delete()
    session.query(Budget).filter(Budget.property_id == prop.id).delete()
    session.query(Asset).filter(Asset.property_id == prop.id).delete()

    # Delete tasks and their schedules
    from mihomes.models.task import TaskSchedule
    task_ids = [t.id for t in session.query(Task.id).filter(Task.property_id == prop.id).all()]
    if task_ids:
        session.query(TaskSchedule).filter(TaskSchedule.task_id.in_(task_ids)).delete(synchronize_session="fetch")
        session.query(Task).filter(Task.property_id == prop.id).delete()

    session.query(Issue).filter(Issue.property_id == prop.id).delete()

    record_change(session, "property", prop.id, "delete", snapshot_instance(prop))
    session.delete(prop)
    session.flush()
    return name


def occupy_property(
    session: Session,
    id_or_slug: str,
    from_date: date | None = None,
    until_date: date | None = None,
) -> Property:
    effective_from = from_date or date.today()
    if until_date and until_date <= effective_from:
        raise ValueError(
            f"occupied_until ({until_date}) must be after occupied_since ({effective_from})"
        )
    prop = update_property(
        session,
        id_or_slug,
        occupied=True,
        occupied_since=effective_from,
        occupied_until=until_date,
    )
    # Auto-generate guest turnover tasks on occupancy
    _run_occupancy_template(session, prop, "guest-turnover")
    return prop


def vacate_property(session: Session, id_or_slug: str) -> Property:
    prop = update_property(
        session,
        id_or_slug,
        occupied=False,
        occupied_since=None,
        occupied_until=None,
    )
    # Auto-generate post-departure turnover tasks on vacate
    _run_occupancy_template(session, prop, "guest-turnover")
    return prop


def _run_occupancy_template(session: Session, prop: Property, template_slug: str) -> None:
    """Silently run a template for a property if the template exists."""
    from mihomes.models.template import Template
    from mihomes.services.template import run_template
    tmpl = session.query(Template).filter(Template.slug == template_slug).first()
    if tmpl:
        run_template(session, template_slug, str(prop.id))
