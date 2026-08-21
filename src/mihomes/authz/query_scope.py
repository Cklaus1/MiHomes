"""Intra-account scoping, applied at the query layer — SPEC-003 §6 Step 7 and U7.

§9.4 step 4: *"filtered to scoped homes **at the query layer**, not post-hoc."* The distinction
is the whole design. Post-hoc filtering means the rows were selected, returned to application
code, and possibly counted, summed, or logged before being dropped — and every surface that
forgets to drop them leaks. A query that never selects them cannot leak them, on any surface,
including ones written later by someone who has never read this file.

**This mirrors SPEC-002's tenant filter deliberately** (`tenancy/session.py`): same
`do_orm_execute` hook, same `with_loader_criteria` mechanism, same read-the-value-outside-the-
lambda requirement. Phase 1 filters *between* accounts; this filters *inside* one. Two layers,
one pattern, so a reader who understands either understands both.

**The model lists are derived from `ENTITY_CLASSES`, never hand-written.** That is what makes N4
("every model must land in one §4.1 class") enforceable rather than aspirational: a new model is
scoped the moment it is classified, and G1's `test_every_model_is_classified` refuses to let it
go unclassified.

---

## U7 — why four of the six classes had no mechanism, and what changed

G1 pinned that every mapped model lands in exactly one §4.1 class. G17 asked the different
question — *which classes does any code actually read?* — and the answer was **one**. This module
derived its list from `PROPERTY_SCOPED` and nothing consulted the rest, so `ACCOUNT_LEVEL`,
`PERSONNEL`, `PROPERTY_LINKED` and `FLAGGED` were labels with no enforcement behind them. A model
classified `ACCOUNT_LEVEL` was not denied to staff *by that classification*; it was denied only
where some route happened to declare an action staff lack. Where a route declared a `SCOPED`
action instead — legitimately — the class said "staff never see this" and the query returned it.

Four leaks came out of that gap, each found by asking which class enforces the rule rather than
trusting that the rule was enforced. `/library/` served another property's books; `/ai/sessions*`
served transcripts; `/search/` returned notes from unscoped properties; `/vendors/` rendered the
vendor ratings D12 denies staff **by name**. The first two were route mistakes and were fixed by
reclassifying and redeclaring. The last two are not: both routes are correctly declared with
actions staff hold, and both read an `ACCOUNT_LEVEL` model from inside a **service** rather than
an endpoint body — which is why a static scan of endpoint sources could not see them.

`redact.py` stated, in a comment, that *"`VendorRating` is classified `ACCOUNT_LEVEL`, so staff
never receive the row."* That was false, and it is the most instructive part of the finding: a
written claim that a classification enforces something, in a file whose whole job is enforcement.
The classification was right. Nothing read it.

## `ACCOUNT_LEVEL` is not one shape but three

A single `with_loader_criteria` cannot cover it the way `tenancy/session.py` covers everything
through the `TenantOwned` base class, because the 20 models divide by what they can be filtered
*on*:

| Shape | Models | Criteria |
|---|---|---|
| Carries `property_id` | `Budget`, `Transaction`, `Contract`, `RecurringExpense`, `InsurancePolicy`, `VendorRating` | `property_id IN scope` |
| Polymorphic parent | `Note`, `TagAssignment`, `AuditLog` | subquery on `entity_type`/`entity_id`, the `_document_criteria` shape |
| No property linkage at all | `Tag`, `Configuration`, `AIConversation`, `Invite`, `OnboardingState`, `TelegramLink`, `Account` | `false()` — deny every row when a staff role is bound |

A NULL `property_id` (nullable on `InsurancePolicy` and `VendorRating`) is excluded by the plain
`in_()`, and the first draft of this module guarded it explicitly with `is_not(None)` on the
theory that `NULL IN (...)` being NULL rather than false left the row's fate undecided. Mutation
testing removed that guard: dropping it turned no test red, and measuring directly showed both
forms returning identically, because a WHERE clause keeps only rows evaluating to **true** and
NULL is not true. The guard is gone. See `_property_id_criteria` for the full note — an
unnecessary condition in a security filter reads as a handled hazard and is worse than none.

## Two guards this listener was missing and `tenancy/session.py` has

**A statement-type gate (N2).** `is_select` alone leaves `session.query(X).delete()` and
`.update()` unscoped. Verified before this change: a scoped staff `query(Note).delete()` removed
every note in the account.

**A short-circuit.** Without one, ~25 criteria objects are constructed for *every* ORM statement
in the process, including the ones touching nothing governed here.

**And the short-circuit cannot be `state.all_mappers`, which is the subtle part.** That collection
is **empty** for `query(M).count()` and `select(func.count()).select_from(M)` — the statement's
top-level column is a bare `count(*)`, not an entity — so a gate written that way silently skips
every count. Measured: a row planted under another account was returned by `.count()` and
correctly absent from `.all()`. So this module walks the statement's expression tree for governed
tables instead, and `TestTheFilterSurvivesCountAndDelete` pins it.

**`tenancy/session.py` has the identical gap and is deliberately left alone.** Two reasons, and
both were established by measurement rather than argument:

1. *RLS covers it.* Probed as `mihomes_test_app`, the non-superuser role production connects as,
   every count shape returns the correct row count. The alarming figure that started this — a
   count of 8 where `.all()` returned 2 — came from a probe connected as `postgres`, and
   superusers bypass RLS unconditionally even under `FORCE ROW LEVEL SECURITY`. So it is a
   defence-in-depth gap on the *account* boundary, not a live leak.
2. *Widening it there breaks authentication.* Attempted, and it turned 44 tests red. `auth/
   sessions.py` resolves a membership with a Core `select(memberships.c.role, ...)` **before any
   account context exists** — resolving the session is how the account gets chosen — and its
   docstring names the empty `all_mappers` as the mechanism that lets it through, while N9
   forbids reaching for `skip_tenant` on the hot path of every request. The narrow gate is
   load-bearing there.

Which makes the same one-line pattern correct in one file and wrong in the other, for a reason
worth stating: **RLS enforces the account boundary and nothing enforces the property boundary
except this module.** A uniform "fix" would have removed a documented bootstrap path to add
redundancy where a stronger enforcer already sits. The tenancy-layer residual is recorded in
`opportunities.md`; it is not a defect to be patched away.

## What this does NOT cover, stated rather than discovered later

- **Child tables with no `property_id`** — `PriceEntry`, `ConsumablePriceEntry`, `TaskSchedule`,
  `EventGuest`, `Guest`. `PROPERTY_SCOPED` by class but carrying no column to filter on, so a
  query loading them *through their parent* is protected by the parent's filter while a direct
  query on the child is not. `PriceEntry` and `ConsumablePriceEntry` are additionally covered by
  redaction (§4.4). Declared in `test_leak_matrix.py`'s `NOT_YET_ENFORCEABLE` and logged in
  `opportunities.md`.
- **Core association tables** — no mapped class, so `with_loader_criteria` cannot reach them. The
  same blind spot `tenancy/session.py` documents, one layer up.
- **`PROPERTY_LINKED` and `FLAGGED`** keep the mechanisms they already had: `Vendor` is
  contact-fields-only through redaction (D12) and `Document` through `_document_criteria` (D13).
  Neither is a row-visibility rule, so neither belongs in the tables above.
"""

from __future__ import annotations

import uuid

from sqlalchemy import event, false
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.sql import visitors

from mihomes.authz.actions import ENTITY_CLASSES, EntityClass
from mihomes.authz.scope import current_property_scope
from mihomes.tenancy.context import current_user

__all__ = ["install_property_scope_listener", "scoped_models"]


#: Models that stay readable despite `ACCOUNT_LEVEL`, and the two reasons are different in kind.
#:
#: `Membership` and `MembershipPropertyScope` are **structural**: `scope.py scoped_property_ids`
#: reads them to *compute* the staff scope, so denying them makes the primitive recursive on its
#: own filter — resolving "which properties may this person see" would require already knowing.
#:
#: `Template` and `TemplateItem` are a **deferral**, removed by U6b. `task.manage` currently
#: grants staff `/templates/` and `test_leak_matrix.py` asserts they reach it; denying the rows
#: before that route is redeclared would make the two commits fail each other. U6b gives
#: templates their own `automation.manage` key and retires these two entries.
_ACCOUNT_LEVEL_EXEMPT = frozenset(
    {"Membership", "MembershipPropertyScope", "Template", "TemplateItem"}
)

#: `entity_type` → the parent model, for the polymorphic `ACCOUNT_LEVEL` models. A superset of
#: `_document_parents` on purpose: a note may hang off an account-level parent (a contract, a
#: budget) where a *document* never usefully does, and such a note must be denied rather than
#: silently unmatched. Any `entity_type` absent from this map matches no branch and is invisible,
#: which is the fail-closed direction for a value this code does not recognise.
_POLYMORPHIC_PARENTS = ("asset", "consumable", "issue", "task", "work_order", "property")


def scoped_models() -> list[tuple[type, str]]:
    """`(model, column_name)` for every `PROPERTY_SCOPED` model this layer can filter.

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


def _models_in_class(entity_class: EntityClass) -> list[type]:
    """Every model in a §4.1 class, minus the documented exemptions.

    Derived from `ENTITY_CLASSES` so that classifying a model is what enforces it. A hardcoded
    list would leave the next model someone adds unprotected — which is exactly how `Book` and
    `VendorRating` came to be classified-but-unenforced.
    """
    return [
        model
        for model, cls in ENTITY_CLASSES.items()
        if cls is entity_class and model.__name__ not in _ACCOUNT_LEVEL_EXEMPT
    ]


def _governed_tables() -> frozenset[str]:
    """Table names this listener has criteria for — the short-circuit's lookup set.

    Built once per call from the classification rather than cached at import: the module is
    imported before some models are, and a stale set would silently stop governing whatever was
    registered late.
    """
    names = {m.__table__.name for m, _ in scoped_models()}
    for cls in (EntityClass.ACCOUNT_LEVEL, EntityClass.PERSONNEL):
        names |= {m.__table__.name for m in _models_in_class(cls)}
    from mihomes.models.document import Document

    names.add(Document.__table__.name)
    return frozenset(names)


def _touches_governed_table(statement) -> bool:
    """Does this statement mention a table this listener governs?

    **A tree walk, deliberately not `state.all_mappers`.** That collection is empty for
    `query(M).count()` and `select(func.count()).select_from(M)`, because the statement's
    top-level column is a bare `count(*)` rather than an entity — so gating on it skips every
    count statement, silently. See the module docstring; this was measured, not inferred.
    """
    governed = _governed_tables()
    for element in visitors.iterate(statement, {"column_collections": False}):
        name = getattr(element, "name", None)
        if isinstance(name, str) and name in governed:
            return True
    return False


def _property_id_criteria(model, allowed):
    """`property_id IN scope`. A NULL `property_id` is excluded, which is the intended reading.

    **The explicit `is_not(None)` guard this function used to carry has been removed, because it
    was decoration.** The reasoning for adding it was that `NULL IN (...)` evaluates to NULL
    rather than false, so a bare `in_()` supposedly left a NULL-property row's fate to the
    surrounding boolean context. Half right: `NULL IN (1)` really is NULL, and Postgres confirms
    it. But a WHERE clause keeps only rows evaluating to **true**, and NULL is not true — so the
    row is excluded either way, and the guard changed no result on any query.

    Mutation testing is what settled it: replacing `and_(is_not(None), in_())` with a bare
    `in_()` turned no test red, and rather than accept that as "the arm has no teeth" the
    behaviour was measured directly — three ratings seeded (NULL / in-scope / out-of-scope), both
    criteria returning exactly `['in_scope']`. An unnecessary condition in a security filter is
    worse than none: it suggests a hazard is being handled and invites the reader to trust the
    surrounding code more than the evidence warrants.

    `InsurancePolicy.property_id` and `VendorRating.property_id` are the nullable ones, and
    `TestVendorRatingsAreNotServedToStaff` seeds `property_id=None` deliberately so this stays
    asserted rather than assumed.
    """
    return model.property_id.in_(allowed)


def _polymorphic_criteria(model, allowed):
    """`entity_type`/`entity_id` pointing at a property-scoped parent inside the scope.

    The same shape as `_document_criteria`, and for the same reason: the model carries no
    `property_id`, so its scope is its parent's. A row whose `entity_id` is NULL, or whose
    `entity_type` is not in `_POLYMORPHIC_PARENTS`, matches no branch and is therefore invisible
    — the fail-closed reading of a case the source never resolves (F2c).

    This is what closes the `/search/` leak: `services/search.py` runs a raw `Note.content ILIKE`
    across the account from behind a route staff legitimately hold.
    """
    from sqlalchemy import and_, or_, select

    from mihomes.models.asset import Asset
    from mihomes.models.consumable import Consumable
    from mihomes.models.issue import Issue
    from mihomes.models.property import Property
    from mihomes.models.task import Task
    from mihomes.models.work_order import WorkOrder

    parents = {
        "asset": Asset,
        "consumable": Consumable,
        "issue": Issue,
        "task": Task,
        "work_order": WorkOrder,
        "property": Property,
    }
    branches = []
    for entity_type in _POLYMORPHIC_PARENTS:
        parent = parents[entity_type]
        column = parent.id if parent.__name__ == "Property" else parent.property_id
        branches.append(
            and_(
                model.entity_type == entity_type,
                model.entity_id.in_(select(parent.id).where(column.in_(allowed))),
            )
        )
    return or_(*branches)


def _account_level_criteria(model, allowed):
    """One of three shapes, chosen by what the model can be filtered on.

    The `false()` branch is the default rather than the exception, and that ordering matters: a
    model with no property linkage is one staff have no scope-based claim to, so *deny* is the
    correct answer and a newly-added `ACCOUNT_LEVEL` model inherits it without anyone editing
    this function.
    """
    columns = set(model.__table__.c.keys())
    if "property_id" in columns:
        return _property_id_criteria(model, allowed)
    if {"entity_type", "entity_id"} <= columns:
        return _polymorphic_criteria(model, allowed)
    return false()


def _personnel_criteria(model, user_id: uuid.UUID | None):
    """§4.1's `PERSONNEL` rule: *"Staff may see their own record; never others'"* (F2d).

    `staff.user_id` (U6a) is what makes this answerable — `Staff.email` cannot, being nullable,
    non-unique, and often not the address someone signs in with.

    **`false()` when no user is bound**, which is the CLI, background jobs and the bot. That is
    D3's zero-scope direction: absent authority denies. The opposite reading — no user, so no
    filter — would make every unattended path a full read of the HR table, and unattended paths
    are exactly the ones nobody is watching.
    """
    if user_id is None:
        return false()
    if model.__name__ == "Staff":
        return model.user_id == user_id
    # `StaffPTORequest` links through `staff_id`, not `user_id` — its own table has no user
    # column, so "mine" resolves one hop out through the staff row.
    from sqlalchemy import select

    from mihomes.models.staff import Staff

    return model.staff_id.in_(select(Staff.id).where(Staff.user_id == user_id))


def _apply_property_scope(execute_state) -> None:
    if execute_state.is_column_load or execute_state.is_relationship_load:
        # Refreshing an already-loaded object, or following a relationship the parent query was
        # already authorised for. Re-filtering here would fight the identity map rather than
        # protect anything.
        return

    # N2 — not `is_select` alone. Without the update/delete arms a scoped staff
    # `query(Note).delete()` removes every note in the account; that was measured, and
    # `test_delete_is_filtered` keeps it measured.
    if not (
        execute_state.is_select or execute_state.is_update or execute_state.is_delete
    ):
        return

    scope = current_property_scope.get()
    if scope is None:
        # Unrestricted: owner/admin, the CLI, background jobs. **Not** the same as an empty
        # scope, which restricts to nothing — see `authz/scope.py`.
        return

    if not _touches_governed_table(execute_state.statement):
        return

    # Read outside the lambda. SQLAlchemy rejects a callable invoked inside a lambda SQL
    # construct outright ("Can't invoke Python callable get() inside of lambda expression"), and
    # `tenancy/session.py` documents the same correction for the tenant filter.
    allowed = list(scope)
    try:
        user_id = current_user.get()
    except LookupError:
        user_id = None

    options = []
    for model, column in scoped_models():
        options.append(
            with_loader_criteria(
                model, getattr(model, column).in_(allowed), include_aliases=True
            )
        )
    for model in _models_in_class(EntityClass.ACCOUNT_LEVEL):
        options.append(
            with_loader_criteria(
                model, _account_level_criteria(model, allowed), include_aliases=True
            )
        )
    for model in _models_in_class(EntityClass.PERSONNEL):
        options.append(
            with_loader_criteria(
                model, _personnel_criteria(model, user_id), include_aliases=True
            )
        )
    options.append(
        with_loader_criteria(_Document(), _document_criteria(allowed), include_aliases=True)
    )

    execute_state.statement = execute_state.statement.options(*options)


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
