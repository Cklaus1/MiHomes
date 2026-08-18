"""G2/G8 · §4.4 — field-level redaction, and the two gates that stop A12 passing vacuously.

F4: *"money lives inside rows staff are permitted to see."* `require_permission` decides
**whether** you get the row, not **which columns**, so "finances ✗ for staff" is unenforceable
without this layer.

**Why `test_money_census_is_complete` and `test_every_redacted_field_exists` exist.** A12's
criterion is *"money is redacted for staff on every `REDACTED_FIELDS` model"* — but
`REDACTED_FIELDS` is the dict under test. It supplies the scope of its own gate, so implementing
§4.4 verbatim makes A12 green while nine of the tree's fifteen `Money` columns leak. Both gates
below derive their scope from the **code** (the `Money` type census, the mapper registry) rather
than from the spec's transcription of it. Conventions §0: *"a gate that cannot fail is not a
gate."*
"""

from __future__ import annotations

import pytest

from mihomes.authz.actions import ENTITY_CLASSES, EntityClass
from mihomes.authz.redact import (
    MONEY_VISIBLE_TO_STAFF,
    REDACTED_FIELDS,
    money_columns,
    redact_for_role,
)


def _mapped_attribute_names(model: type) -> set[str]:
    """Column and relationship names on a model, as SQLAlchemy sees them."""
    from sqlalchemy import inspect as sa_inspect

    mapper = sa_inspect(model)
    return {attr.key for attr in mapper.attrs}


class TestRedactionTableIntegrity:
    """The two derived gates. Neither can be satisfied by editing the spec's dict alone."""

    def test_every_redacted_field_exists(self):
        """G-exists — a frozenset of attribute names is not type-checked.

        A misspelled or removed field redacts **nothing, silently**, and A12 still passes because
        the loop over `REDACTED_FIELDS` simply never matches. Five of §4.4's seventeen names did
        not exist when this phase started (`WorkOrder.cost`, `WorkOrder.invoice_number`,
        `Asset.value`, `Consumable.last_order_cost`, `Task.estimated_cost`), which is what this
        gate is here to make impossible to reintroduce.
        """
        problems = []
        for model, fields in REDACTED_FIELDS.items():
            known = _mapped_attribute_names(model)
            for field in fields:
                if field not in known:
                    problems.append(f"{model.__name__}.{field}")
        assert not problems, (
            "REDACTED_FIELDS names attributes that do not exist, so they redact nothing: "
            f"{sorted(problems)}"
        )

    def test_money_census_is_complete(self):
        """G-census — every `Money` column must be *accounted for*, not merely unredacted.

        Three admissible outcomes per column, and silence is not one of them:

        1. **redacted** — the column is in `REDACTED_FIELDS`;
        2. **row-denied** — staff never receive the row at all, because the model's entity class
           is account-level, personnel, or global. Derived from `ENTITY_CLASSES` rather than
           hand-listed, so it cannot drift from the classification;
        3. **explicitly visible** — an entry in `MONEY_VISIBLE_TO_STAFF` carrying a reason.

        A new money column on a property-scoped model fails the suite until someone decides which
        of the three it is. That is the whole point: `Event.budget` leaked precisely because
        nobody had to decide.
        """
        row_denied_classes = {
            EntityClass.ACCOUNT_LEVEL,
            EntityClass.PERSONNEL,
            EntityClass.GLOBAL,
        }
        unclassified = []
        for model, column_name in money_columns():
            if column_name in REDACTED_FIELDS.get(model, frozenset()):
                continue
            if ENTITY_CLASSES.get(model) in row_denied_classes:
                continue
            if (model, column_name) in MONEY_VISIBLE_TO_STAFF:
                continue
            unclassified.append(f"{model.__name__}.{column_name}")

        assert not unclassified, (
            "every Money column must be redacted, row-denied by its entity class, or "
            "explicitly allowlisted with a reason — unclassified: "
            f"{sorted(unclassified)}"
        )

    def test_every_allowlist_entry_carries_a_reason(self):
        """An allowlist without reasons is a list of things nobody has to justify."""
        for key, reason in MONEY_VISIBLE_TO_STAFF.items():
            assert isinstance(reason, str) and reason.strip(), (
                f"MONEY_VISIBLE_TO_STAFF[{key}] needs a one-line reason"
            )

    def test_the_census_actually_finds_money_columns(self):
        """A guard on the guard: if `money_columns()` returned nothing, the census above would
        pass trivially and every money field in the tree would be unprotected.

        Measured at pre-flight: 15 `Money` columns across the schema.
        """
        found = list(money_columns())
        assert len(found) >= 15, (
            f"the Money census found only {len(found)} columns — it is not seeing the schema"
        )

    def test_property_scoped_money_is_actually_redacted(self):
        """The census's teeth, stated positively.

        Every money column on a model staff *can* see must be in `REDACTED_FIELDS` — the
        row-denied arm must not be able to absorb a property-scoped model. Without this, moving a
        model into `ACCOUNT_LEVEL` would silence the census rather than fix the leak.
        """
        leaked = []
        for model, column_name in money_columns():
            if ENTITY_CLASSES.get(model) not in {
                EntityClass.PROPERTY_SCOPED,
                EntityClass.PROPERTY_LINKED,
                EntityClass.FLAGGED,
            }:
                continue
            if column_name in REDACTED_FIELDS.get(model, frozenset()):
                continue
            if (model, column_name) in MONEY_VISIBLE_TO_STAFF:
                continue
            leaked.append(f"{model.__name__}.{column_name}")
        assert not leaked, (
            "money on a row staff are permitted to see must be redacted (F4/D14): "
            f"{sorted(leaked)}"
        )


class TestRedactForRole:
    """`redact_for_role` itself — one function, called by both the web serializer and the AI
    context builder (N3: *"Do not redact in templates"*, because the AI path renders none)."""

    def _work_order(self):
        from mihomes.models.work_order import WorkOrder

        return WorkOrder(title="Fix boiler", estimated_cost=250.0, actual_cost=310.0)

    @pytest.mark.parametrize("role", ["owner", "admin"])
    def test_redact_is_identity_for_privileged(self, role):
        """owner/admin pass through unchanged — the object itself, not a copy.

        Identity matters: a copy would silently detach the row from its session and break lazy
        loads in every template that renders one.
        """
        work_order = self._work_order()
        assert redact_for_role(work_order, role) is work_order

    def test_money_hidden_for_staff(self):
        """A12's core assertion, at the function level."""
        view = redact_for_role(self._work_order(), "staff")
        assert view.estimated_cost is None
        assert view.actual_cost is None

    def test_non_money_fields_survive_redaction(self):
        """Staff still need the row to do their job (D14: *"redacted, not row-denied"*).

        A redactor that blanked everything would pass the money assertions and destroy the
        feature — the housekeeper could no longer see which work order to do.
        """
        view = redact_for_role(self._work_order(), "staff")
        assert view.title == "Fix boiler"

    def test_redaction_does_not_mutate_the_underlying_row(self):
        """**The dangerous failure mode.** Redacting by setting attributes to `None` on the ORM
        object would enqueue an UPDATE, and the next flush would write the nulls to the database
        — turning a display concern into permanent data loss. The view must be a wrapper.
        """
        work_order = self._work_order()
        redact_for_role(work_order, "staff")
        assert work_order.estimated_cost == 250.0
        assert work_order.actual_cost == 310.0

    def test_redacted_view_refuses_writes(self):
        """A staff-facing view must not be a write path back onto the row."""
        view = redact_for_role(self._work_order(), "staff")
        with pytest.raises(AttributeError):
            view.actual_cost = 1.0

    def test_unknown_role_is_treated_as_staff(self):
        """Fail closed on an unrecognised role.

        A role string arriving from a future migration, a typo, or a bot path that never set one
        must not be handed the finances. The permissive default is the one that cannot be caught
        by a test that only checks the three known roles.
        """
        view = redact_for_role(self._work_order(), "something-new")
        assert view.actual_cost is None

    def test_model_with_no_redaction_entry_passes_through(self):
        """Models absent from `REDACTED_FIELDS` are returned as-is.

        Their protection is row-level (the entity classification), not field-level; wrapping them
        anyway would add a proxy to every object in the system for no benefit.
        """
        from mihomes.models.task import Task

        task = Task(title="Sweep the terrace")
        assert redact_for_role(task, "staff") is task


class TestTransitiveRedaction:
    """Redaction must survive relationship traversal, because the AI executors traverse.

    `_query_work_orders` (`services/ai/tools.py:530`) renders `wo.vendor.company_name` and
    `wo.property.name`. A proxy that redacted only the work order would return a **raw** `Vendor`
    from `wo.vendor`, complete with the `insurance_info` and `license_number` D12 denies staff —
    a leak reached one attribute hop from a permitted row, through the exact call shape the
    highest-risk step in the phase (Step 10) is built on.
    """

    def test_related_object_is_redacted_too(self):
        from mihomes.models.vendor import Vendor
        from mihomes.models.work_order import WorkOrder

        work_order = WorkOrder(title="Pest control", actual_cost=400.0)
        work_order.vendor = Vendor(
            company_name="Orkin", license_number="LIC-9", insurance_info="Policy 123",
        )

        view = redact_for_role(work_order, "staff")
        assert view.vendor.company_name == "Orkin"
        assert view.vendor.license_number is None, (
            "traversing to a related model must not escape redaction"
        )
        assert view.vendor.insurance_info is None

    def test_related_collection_is_redacted_elementwise(self):
        """`Asset.price_entries` is the same shape one level down.

        The parent's collection is in `REDACTED_FIELDS`, so it returns `None` — but a caller that
        reaches `PriceEntry` rows another way must still not see `price`.
        """
        from mihomes.models.asset import Asset, PriceEntry

        asset = Asset(name="Boiler", purchase_price=5000.0)
        entry = PriceEntry(price=5000.0, quantity=1.0)

        view = redact_for_role(asset, "staff")
        assert view.purchase_price is None
        assert view.price_entries is None  # the collection itself is redacted

        entry_view = redact_for_role(entry, "staff")
        assert entry_view.price is None
        assert entry_view.quantity == 1.0

    def test_privileged_traversal_is_untouched(self):
        """The negative control — an admin must still reach the nested sensitive fields.

        Without this, a redactor that wrapped everything for every role would pass the two tests
        above and quietly break the owner's view of their own estate.
        """
        from mihomes.models.vendor import Vendor
        from mihomes.models.work_order import WorkOrder

        work_order = WorkOrder(title="Pest control", actual_cost=400.0)
        work_order.vendor = Vendor(company_name="Orkin", license_number="LIC-9")

        view = redact_for_role(work_order, "admin")
        assert view.vendor.license_number == "LIC-9"
        assert view.actual_cost == 400.0

    def test_scalar_attributes_are_not_wrapped(self):
        """Only mapped instances get proxied; plain values pass through as themselves.

        A wrapper around every string would break `.startswith`, `len()`, and every format spec
        in the executors' f-strings.
        """
        from mihomes.models.work_order import WorkOrder

        view = redact_for_role(WorkOrder(title="Fix boiler", actual_cost=1.0), "staff")
        assert isinstance(view.title, str)


class TestVendorContactOnly:
    """A13 · D12 — staff see vendor contact information only, read-only."""

    def _vendor(self):
        from mihomes.models.vendor import Vendor

        return Vendor(
            company_name="Orkin", contact_name="Dana", phone="+1-555-0100",
            email="dana@orkin.example", license_number="LIC-9",
            insurance_info="Policy 123", notes="Prefers morning visits",
        )

    def test_vendor_contact_only(self):
        """The four contact fields survive; the three sensitive ones do not."""
        view = redact_for_role(self._vendor(), "staff")

        assert view.company_name == "Orkin"
        assert view.contact_name == "Dana"
        assert view.phone == "+1-555-0100"
        assert view.email == "dana@orkin.example"

        assert view.license_number is None
        assert view.insurance_info is None
        assert view.notes is None

    def test_admin_sees_everything(self):
        """The other half of A13 — redaction that hid these from admins would be a bug, not
        caution. D12 restricts *staff*, and a test asserting only absence would pass against a
        redactor that hid the fields from everyone."""
        vendor = self._vendor()
        view = redact_for_role(vendor, "admin")
        assert view.license_number == "LIC-9"
        assert view.insurance_info == "Policy 123"
