"""Intra-account property scoping, applied at the query layer — SPEC-003 §6 Step 7.

§9.4 step 4: *"filtered to scoped homes **at the query layer**, not post-hoc."* The distinction
is the whole design. Post-hoc filtering means the rows were selected, returned to application
code, and possibly counted, summed, or logged before being dropped — and every surface that
forgets to drop them leaks. A query that never selects them cannot leak them, on any surface,
including ones written later by someone who has never read this file.

**This mirrors SPEC-002's tenant filter deliberately** (`tenancy/session.py`): same
`do_orm_execute` hook, same `with_loader_criteria` mechanism, same read-the-value-outside-the-
lambda requirement. Phase 1 filters *between* accounts; this filters *inside* one. Two layers,
one pattern, so a reader who understands either understands both.

**The model list is derived from `ENTITY_CLASSES`, not hand-written.** That is what makes N4
("every model must land in one §4.1 class") enforceable rather than aspirational: a new
property-scoped model is scoped the moment it is classified, and G1's
`test_every_model_is_classified` refuses to let it go unclassified.

**What this does NOT cover, stated rather than discovered later:**

- **Child tables with no `property_id`** — `PriceEntry`, `ConsumablePriceEntry`, `TaskSchedule`,
  `EventGuest`, `Guest`. They are `PROPERTY_SCOPED` by class but carry no column to filter on, so
  a query loading them *through their parent* is protected by the parent's filter, while a direct
  query on the child is not. `PriceEntry` and `ConsumablePriceEntry` are additionally covered by
  redaction (§4.4). Logged in `opportunities.md`.
- **Core association tables** — no mapped class, so `with_loader_criteria` cannot reach them. The
  same blind spot `tenancy/session.py` documents, one layer up.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from mihomes.authz.actions import ENTITY_CLASSES, EntityClass
from mihomes.authz.scope import current_property_scope

__all__ = ["install_property_scope_listener", "scoped_models"]


def scoped_models() -> list[tuple[type, str]]:
    """`(model, column_name)` for every model this layer can filter.

    `Property` is scoped by its **own** `id` — it *is* the property — while everything else is
    scoped by its `property_id` foreign key. Getting that wrong would either leave the property
    list unscoped (a leak: staff would see every property's name) or filter it to nothing.
    """
    models: list[tuple[type, str]] = []
    for model, entity_class in ENTITY_CLASSES.items():
        if entity_class is not EntityClass.PROPERTY_SCOPED:
            continue
        if model.__name__ == "Property":
            models.append((model, "id"))
        elif hasattr(model, "property_id"):
            models.append((model, "property_id"))
        # Children without a property_id are covered through their parent; see the module
        # docstring. Silently skipping them here would be the bug — it is recorded there instead.
    return models


def _apply_property_scope(execute_state) -> None:
    if execute_state.is_column_load or execute_state.is_relationship_load:
        # Refreshing an already-loaded object, or following a relationship the parent query was
        # already authorised for. Re-filtering here would fight the identity map rather than
        # protect anything.
        return

    scope = current_property_scope.get()
    if scope is None:
        # Unrestricted: owner/admin, the CLI, background jobs. **Not** the same as an empty
        # scope, which restricts to nothing — see `authz/scope.py`.
        return

    # Read outside the lambda. SQLAlchemy rejects a callable invoked inside a lambda SQL
    # construct outright ("Can't invoke Python callable get() inside of lambda expression"), and
    # `tenancy/session.py` documents the same correction for the tenant filter.
    allowed = list(scope)

    for model, column in scoped_models():
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                model,
                getattr(model, column).in_(allowed),
                include_aliases=True,
            )
        )

    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(_Document(), _document_criteria(allowed), include_aliases=True)
    )


def _Document():  # noqa: N802 - reads as the class it stands in for
    from mihomes.models.document import Document

    return Document


#: `entity_type` → the parent model, for the polymorphic link. Only **property-scoped** parents
#: appear: a document on a `contract` or an `insurance` policy hangs off an account-level row that
#: staff never receive, so there is no scope under which it becomes visible.
def _document_parents() -> dict[str, type]:
    from mihomes.models.asset import Asset
    from mihomes.models.consumable import Consumable
    from mihomes.models.issue import Issue
    from mihomes.models.property import Property
    from mihomes.models.task import Task
    from mihomes.models.work_order import WorkOrder

    return {
        "asset": Asset,
        "consumable": Consumable,
        "issue": Issue,
        "task": Task,
        "work_order": WorkOrder,
        "property": Property,
    }


def _document_criteria(allowed):
    """D13 + C11 — `staff_visible` **and** a parent inside the scope.

    Both conditions, ANDed. Filtering on the flag alone would let a ticked document on another
    property through; filtering on scope alone would expose every invoice by default. D13 is the
    first condition and G7's scoping is the second, and the document layer is the one place they
    have to be spelled out together because `Document` carries no `property_id` of its own (C11).

    A document with `entity_id IS NULL` matches no branch and is therefore invisible: an
    account-level document has no parent whose scope could authorise it. That is the fail-closed
    reading of a case the source never resolves (F2c).
    """
    from sqlalchemy import and_, or_, select

    from mihomes.models.document import Document

    branches = []
    for entity_type, parent in _document_parents().items():
        column = parent.id if parent.__name__ == "Property" else parent.property_id
        branches.append(
            and_(
                Document.entity_type == entity_type,
                Document.entity_id.in_(select(parent.id).where(column.in_(allowed))),
            )
        )

    return and_(Document.staff_visible.is_(True), or_(*branches))


def install_property_scope_listener() -> None:
    """Idempotent — the module is imported from several places and must not stack listeners."""
    if not event.contains(Session, "do_orm_execute", _apply_property_scope):
        event.listen(Session, "do_orm_execute", _apply_property_scope)


# Installed at import, matching `tenancy.session`'s convention: anything that reaches the authz
# package gets the behaviour rather than having to remember to switch it on.
install_property_scope_listener()
