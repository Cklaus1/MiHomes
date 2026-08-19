"""Field-level redaction — SPEC-003 §4.4 (A12, A13), with the census that keeps it honest.

F4: *"money lives inside rows staff are permitted to see."* Matrix rows 6/7 grant staff scoped
work orders, assets, and inventory; row 9 denies finances. They collide on the same records — a
housekeeper who may see a work order can see its cost. `require_permission` cannot express this:
it decides *whether* you get the row, not *which columns*.

**Applied in both the web serializer and the AI context builder, via this one function** (N3).
Redaction in Jinja would leave the AI path — which renders no templates — unprotected, which is
F3's exact shape.

**Pre-flight correction (C8).** §4.4's dict named six attributes that do not exist on their
models: `WorkOrder.cost`, `WorkOrder.invoice_number`, `Asset.value`, `Consumable.last_order_cost`,
`Task.estimated_cost` (`Task` has no money column at all — `estimated_hours` is `Float` hours),
and `Vendor.ratings` (`VendorRating.vendor` carries no `back_populates`, so the reverse attribute
was never created). A frozenset of names that do not resolve redacts nothing, silently, and A12
still passes because its loop simply never matches. `test_every_redacted_field_exists` is what
makes that class of error impossible to reintroduce.

**D12's ratings clause is enforced by row-denial, not by this table.** Since `Vendor.ratings` does
not exist, there is no field to strip; `VendorRating` is classified `ACCOUNT_LEVEL`, so staff
never receive the row. Recorded here because "the field is missing from the redaction list" and
"the data is protected" are different claims, and only the second one is true.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from mihomes.type.money import Money

__all__ = [
    "MONEY_VISIBLE_TO_STAFF",
    "REDACTED_FIELDS",
    "RedactedView",
    "money_columns",
    "redact_context",
    "redact_for_role",
]

_PRIVILEGED_ROLES = frozenset({"owner", "admin"})


def _redacted_fields() -> dict[type, frozenset[str]]:
    """Built in a function to keep the model imports local to the table."""
    from mihomes.models.asset import Asset, PriceEntry
    from mihomes.models.consumable import Consumable, ConsumablePriceEntry
    from mihomes.models.contract import Contract
    from mihomes.models.event import Event
    from mihomes.models.vendor import Vendor
    from mihomes.models.work_order import WorkOrder

    return {
        # §4.4 as corrected by C8 — `cost` and `invoice_number` do not exist on WorkOrder.
        WorkOrder: frozenset({"estimated_cost", "actual_cost"}),
        # `value` does not exist; `replacement_cost_estimate` is a Money column §4.4 missed.
        Asset: frozenset({"purchase_price", "replacement_cost_estimate", "price_entries"}),
        # The child tables. Each carries a Money column one relationship hop from a row staff may
        # see — redacting only the parent's collection would still leave the entries reachable
        # from any query that loads them directly.
        PriceEntry: frozenset({"price"}),
        # `last_order_cost` does not exist; the price history is the real exposure.
        Consumable: frozenset({"unit_price", "price_entries"}),
        ConsumablePriceEntry: frozenset({"price"}),
        # Event is property-scoped — staff see the row — and `budget` is a Money column §4.4
        # never mentions. F4's exact shape, missed by the table written to close it (C9).
        Event: frozenset({"budget"}),
        # Contract is account-level, so staff never receive the row; redacting anyway is
        # defence in depth against a future reclassification.
        Contract: frozenset({"cost", "billing_frequency"}),
        # D12 — staff get company_name, contact_name, phone, email, contacts. Never these.
        Vendor: frozenset({"insurance_info", "license_number", "notes"}),
    }


REDACTED_FIELDS: dict[type, frozenset[str]] = _redacted_fields()


# `(model, column_name) -> reason`. Deliberately empty: every Money column in the tree is either
# redacted above or lives on a model whose entity class denies staff the row outright. An entry
# here is a decision that staff may see a specific amount, and it must say why.
MONEY_VISIBLE_TO_STAFF: dict[tuple[type, str], str] = {}


def money_columns() -> Iterator[tuple[type, str]]:
    """Every `Money`-typed column on every application model, read from the mappers.

    The census is derived from the schema rather than transcribed, which is what makes
    `test_money_census_is_complete` a gate rather than a restatement of `REDACTED_FIELDS`. A new
    money column appears here the moment it is declared, and fails the suite until someone
    decides whether staff may see it.
    """
    from mihomes.models import Base

    for mapper in Base.registry.mappers:
        model = mapper.class_
        if not model.__module__.startswith("mihomes.models"):
            # Test fixtures declared on the process-global registry are not application schema.
            continue
        for column in mapper.columns:
            if isinstance(column.type, Money):
                # `column.key` is the attribute name; `column.name` is the DB column. They differ
                # whenever a mapping renames, and every consumer here wants the attribute.
                yield model, column.key


class RedactedView:
    """A read-only proxy that returns `None` for redacted attributes, **transitively**.

    **A wrapper rather than in-place nulling, and that is not a style preference.** Setting the
    attributes to `None` on the ORM object would enqueue an UPDATE, and the next flush would
    write those nulls to the database — turning a display concern into permanent data loss. The
    proxy also refuses writes, so a staff-facing view can never become a path back onto the row.

    **Traversal is the leak a flat proxy misses, and the AI executors traverse constantly.**
    `_query_work_orders` (`services/ai/tools.py:530`) renders `wo.vendor.company_name` and
    `wo.property.name`; a proxy that redacted only the work order would hand back a *raw*
    `Vendor`, complete with `insurance_info` and `license_number` that D12 denies staff. So any
    mapped instance reached through this view — or any collection of them — is itself wrapped at
    the same role. `Asset.price_entries` is the same shape one level down: the parent's
    collection is redacted, and so is every `PriceEntry` inside it.

    Attribute access otherwise falls through unchanged, so templates, `getattr`, and the AI
    context builders work on the fields that survive.
    """

    __slots__ = ("_obj", "_hidden", "_role")

    def __init__(self, obj: Any, hidden: frozenset[str], role: str):
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_hidden", hidden)
        object.__setattr__(self, "_role", role)

    def __getattr__(self, name: str) -> Any:
        if name in object.__getattribute__(self, "_hidden"):
            return None
        value = getattr(object.__getattribute__(self, "_obj"), name)
        return _redact_value(value, object.__getattribute__(self, "_role"))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"{type(self).__name__} is read-only; {name!r} cannot be set on a redacted view"
        )

    def __eq__(self, other: Any) -> bool:
        """Compare as the wrapped row, so a view and its object are interchangeable in tests
        and in `in` checks over collections the caller already held."""
        if isinstance(other, RedactedView):
            other = object.__getattribute__(other, "_obj")
        return object.__getattribute__(self, "_obj") == other

    def __hash__(self) -> int:
        return hash(object.__getattribute__(self, "_obj"))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Redacted {type(object.__getattribute__(self, '_obj')).__name__}>"


def redact_context(context: dict[str, Any], role: str | None) -> dict[str, Any]:
    """Redact every mapped instance in a template context.

    **This is the "web serializer" half of §4.4's "applied in BOTH surfaces".** This application
    renders Jinja templates rather than serialising JSON, so the context dict handed to the
    renderer *is* the serialization boundary — the last place a row exists as an object before it
    becomes markup.

    Doing it here rather than in the templates is N3: *"Do not redact in templates. The AI path
    renders no templates, so template-level redaction leaves it unprotected."* This calls the same
    `redact_for_role` the AI context builder will call at Step 10, so a field added to one surface
    cannot be forgotten on the other.

    `role=None` means no request context (the CLI, a background job) and passes through
    unchanged — those paths have no user to redact for, and are already tenant-scoped.
    """
    if role is None or role in _PRIVILEGED_ROLES:
        return context
    return {key: _redact_value(value, role) for key, value in context.items()}


def _is_mapped_instance(value: Any) -> bool:
    """True for a SQLAlchemy-mapped ORM instance.

    Uses the instrumentation state rather than `isinstance(value, Base)` so it stays correct if a
    model ever picks up a second base, and returns False for plain values without importing every
    model to check.
    """
    return hasattr(type(value), "__mapper__")


def _redact_value(value: Any, role: str) -> Any:
    """Wrap mapped instances (and collections of them) reached through a redacted view."""
    if _is_mapped_instance(value):
        return redact_for_role(value, role)
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_redact_value(item, role) for item in value]
        # Preserve list-ness for templates that index or `|length` the result. Sets of ORM rows
        # are rare; normalising them to a list is safer than rebuilding a set of proxies.
        return items if isinstance(value, (list, set, frozenset)) else tuple(items)
    return value


def redact_for_role(obj: Any, role: str) -> Any:
    """Strip money and sensitive fields for staff; owner/admin pass through unchanged.

    Returns the object **itself** for privileged roles rather than a copy — a copy would detach
    the row from its session and break lazy loads in every template that renders one.

    **An unrecognised role is treated as staff.** A role string arriving from a future migration,
    a typo, or a bot path that never set one must not be handed the finances; the permissive
    default is the one no test of the three known roles would catch. D16 relies on this directly:
    an unlinked Telegram sender is staff-level.
    """
    if role in _PRIVILEGED_ROLES:
        return obj

    hidden = REDACTED_FIELDS.get(type(obj), frozenset())
    if not hidden and not _has_redactable_relationships(type(obj)):
        # Models with neither field-level redaction nor a path to a redacted model are protected
        # at the row level by the entity classification instead. Returning them bare keeps the
        # proxy off the great majority of objects in the system.
        return obj

    return RedactedView(obj, hidden, role)


def _has_redactable_relationships(model: type) -> bool:
    """Whether traversing from `model` can reach a model that *is* redacted.

    Without this, a model absent from `REDACTED_FIELDS` is returned bare and every redacted row
    hanging off it becomes reachable in the clear — `Issue.work_order.actual_cost` would leak
    even though `WorkOrder` is redacted, because `Issue` itself has no money.

    Cached per model: it walks the mapper's relationships, which is stable for the process.
    """
    cached = _RELATIONSHIP_CACHE.get(model)
    if cached is None:
        cached = _compute_has_redactable_relationships(model)
        _RELATIONSHIP_CACHE[model] = cached
    return cached


_RELATIONSHIP_CACHE: dict[type, bool] = {}


def _compute_has_redactable_relationships(model: type) -> bool:
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.exc import NoInspectionAvailable

    try:
        mapper = sa_inspect(model)
    except NoInspectionAvailable:  # pragma: no cover - non-mapped object
        return False
    if mapper is None:  # pragma: no cover - defensive
        return False

    for relationship in mapper.relationships:
        if relationship.mapper.class_ in REDACTED_FIELDS:
            return True
    return False
