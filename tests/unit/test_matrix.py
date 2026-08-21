"""G1 · §6 Step 1 — the capability matrix as data (A1, A2, A3) + C10 classification.

F2's finding is that `ONBOARDING:244-265`'s matrix **is not a lookup table**: every action is an
English verb phrase, values are three-valued, and two cells carry prose caveats *inside the cell*.
§9.4 step 3 nonetheless says to "look up `(role, action)` in the capability matrix." Encoding it
is spec work, and these tests are what hold the encoding to the source.

**A1 is a traceability test, not a count test.** Asserting `len(MATRIX) == 21` would pass if two
keys both claimed row 5 and row 8 were dropped. Asserting the *set of row numbers* is exactly
1..20 is what proves no source row was lost — which matters because row 8 is deliberately split
in two (D12), so key count and row count differ by design.
"""

from __future__ import annotations

import pytest

from mihomes.authz.actions import (
    ENTITY_CLASSES,
    EXTRA_RULES,
    MATRIX,
    Access,
    EntityClass,
    Grant,
)


class TestMatrixCoverage:
    def test_all_twenty_rows_covered(self):
        """A1 — every `ONBOARDING` §9.2 row 1-20 is represented in `MATRIX`."""
        rows = {spec.row for spec in MATRIX.values()}
        assert rows == set(range(1, 21)), (
            f"rows missing from MATRIX: {sorted(set(range(1, 21)) - rows)}; "
            f"unexpected rows: {sorted(rows - set(range(1, 21)))}"
        )

    def test_row_eight_is_split_in_two(self):
        """D12 — row 8 cannot be expressed by one cell.

        Staff get read access to *some* vendor fields and no write access at all; a single
        three-valued cell has no way to say that. The `row` field is what preserves traceability
        back to the source after the split.

        **The `len(MATRIX)` assertion that used to close this test has moved** to
        `test_the_key_count_is_what_the_splits_imply`. It was incidental to the claim here — this
        test is about row 8's *shape* — and leaving it meant every future split had to edit a test
        whose name promised something else, which is how a count silently becomes the thing being
        asserted instead of the split.
        """
        row_eight = sorted(k for k, s in MATRIX.items() if s.row == 8)
        assert row_eight == ["vendor.manage", "vendor.view_contact"]
        assert MATRIX["vendor.view_contact"].staff is Grant.SCOPED
        assert MATRIX["vendor.manage"].staff is Grant.DENY

    def test_the_key_count_is_what_the_splits_imply(self):
        """23 keys for 20 rows: three rows are split, each for a reason recorded at its entry.

        Derived rather than hardcoded, so the number cannot drift away from its justification. A
        bare `len(MATRIX) == 23` would also pass if a split row lost a key while an unrelated row
        gained one — the same class of false green A1 avoids by asserting the row *set* instead of
        a count.

        The three splits: row 8 (D12 — staff read some vendor fields, write none), row 10 (U6b —
        `staff.view_own` alongside `member.manage`, because "own record only" is not "manage
        members"), row 5 (U6b — `automation.manage` alongside `task.manage`, because running a
        template is task work and managing one is not).
        """
        from collections import Counter

        per_row = Counter(spec.row for spec in MATRIX.values())
        split_rows = {row: n for row, n in per_row.items() if n > 1}

        assert split_rows == {5: 2, 8: 2, 10: 2}, (
            "the set of split rows changed. Each split needs a written reason at its MATRIX "
            f"entry, because it breaks the one-key-per-source-row correspondence. Now: {split_rows}"
        )
        assert len(MATRIX) == 20 + sum(n - 1 for n in split_rows.values()) == 23

    def test_key_matches_its_own_spec_key_field(self):
        """A transcription guard: the dict key and `ActionSpec.key` must agree.

        They are written twice in §4.1's source, so they can disagree — and a lookup by dict key
        that returns a spec naming a *different* action would authorise the wrong thing while
        looking correct in every test that goes through the dict.
        """
        for key, spec in MATRIX.items():
            assert spec.key == key, f"MATRIX[{key!r}].key is {spec.key!r}"

    def test_owner_is_never_weaker_than_admin_or_staff(self):
        """A monotonicity invariant the source implies but never states.

        Not decoration: it catches a transposed column, which is the most likely way a hand-typed
        21-row table goes wrong, and a transposition that *widens* staff is a silent grant.
        """
        rank = {Grant.DENY: 0, Grant.SCOPED: 1, Grant.ALLOW: 2}
        for key, spec in MATRIX.items():
            assert rank[spec.owner] >= rank[spec.admin], f"{key}: admin outranks owner"
            assert rank[spec.admin] >= rank[spec.staff], f"{key}: staff outranks admin"

    def test_scoped_grants_are_never_declared_on_account_routes(self):
        """`Access.ACCOUNT` means no property target exists, so `SCOPED` is unsatisfiable there.

        A `SCOPED` grant on an account-class action could only ever be resolved by ignoring the
        scope — i.e. by silently granting. `gateway.link_self` is `ALLOW`/ACCOUNT and is fine;
        what this forbids is `SCOPED`/ACCOUNT.
        """
        for key, spec in MATRIX.items():
            if spec.access is Access.ACCOUNT:
                assert Grant.SCOPED not in (spec.owner, spec.admin, spec.staff), (
                    f"{key} is ACCOUNT-class but carries a SCOPED grant, which has "
                    "no target to scope by"
                )


class TestHoistedRules:
    """The two caveats F2 says no lookup table can hold."""

    def test_rule_change_role(self):
        """A2 · R1 — row 13's "(not owner's, not own)".

        Four distinct cases, because the caveat is two prohibitions with different subjects: an
        admin may not touch the *owner's* role (a privilege-escalation guard) and no one may
        touch *their own* (ownership moves only by D2 transfer, never by self-promotion).
        """
        rule = EXTRA_RULES["R1"]
        admin, owner, other = "m-admin", "m-owner", "m-other"

        # An admin may not change the active owner's role.
        assert not rule(
            actor_role="admin", actor_membership_id=admin,
            target_role="owner", target_membership_id=owner,
        )
        # An admin may not change their own role.
        assert not rule(
            actor_role="admin", actor_membership_id=admin,
            target_role="admin", target_membership_id=admin,
        )
        # An admin may change another admin's or a staff member's role.
        assert rule(
            actor_role="admin", actor_membership_id=admin,
            target_role="staff", target_membership_id=other,
        )
        # The owner may change anyone's role except their own.
        assert rule(
            actor_role="owner", actor_membership_id=owner,
            target_role="admin", target_membership_id=other,
        )
        assert not rule(
            actor_role="owner", actor_membership_id=owner,
            target_role="owner", target_membership_id=owner,
        ), "D2: ownership moves only by transfer, never by self-role-change"

    def test_rule_link_self(self):
        """A3 · R2 — row 20's "(scoped access applies)".

        Two independent claims in one cell: linking is **self-only for every role** (an owner may
        not link a gateway on a staff member's behalf), and the link **grants no additional data
        access** — every resolved request re-enters `require_permission` with the staff role.
        """
        rule = EXTRA_RULES["R2"]
        for role in ("owner", "admin", "staff"):
            assert rule(actor_role=role, actor_membership_id="m1", target_membership_id="m1")
            assert not rule(
                actor_role=role, actor_membership_id="m1", target_membership_id="m2"
            ), f"{role} must not link a gateway on another member's behalf"

    def test_staff_may_link_their_own_gateway(self):
        """R2's first half — row 20 is the one action where staff are `ALLOW`, not `SCOPED`."""
        assert MATRIX["gateway.link_self"].staff is Grant.ALLOW
        assert MATRIX["gateway.link_self"].rule == "R2"

    def test_every_declared_rule_reference_resolves(self):
        """A spec that names `rule="R3"` with no implementation must fail loudly.

        Otherwise the caveat silently does not apply and the action falls back to its plain
        three-valued grant — which is the permissive direction for both R1 and R2.
        """
        for key, spec in MATRIX.items():
            if spec.rule is not None and spec.rule in {"R1", "R2"}:
                assert spec.rule in EXTRA_RULES, f"{key} names unimplemented rule {spec.rule}"


class TestEntityClassification:
    """C10 — N4: "Every model must land in one §4.1 class."

    §4.1's table names about 22 models; the tree has 42 mapped classes. The omissions are not
    cosmetic: `InsurancePolicy` is money-bearing and property-scoped, `VendorRating` is a model
    D12 explicitly denies staff, and `PriceEntry`/`ConsumablePriceEntry` each carry a `Money`
    column one relationship hop from a row staff are permitted to see.
    """

    @staticmethod
    def _application_models() -> set[type]:
        """Every mapped class that belongs to the application, excluding test fixtures.

        **Why the module filter is necessary, and why it is not a loophole.** `Base.registry` is
        process-global, so any test that declares a throwaway model registers it for the whole
        session — `tests/unit/test_slug.py:25` defines `DummyModel` exactly that way. Without
        this filter the gate passes when `test_matrix.py` runs alone and fails when the unit
        suite runs together, which is the worst kind of gate: one whose result depends on
        collection order.

        Filtering on the `mihomes.models` prefix keeps it fail-closed for the thing it guards —
        a genuinely new application model still lands here unclassified and still fails — while
        refusing to let a test fixture dictate the production classification.
        """
        from mihomes.models import Base

        return {
            mapper.class_
            for mapper in Base.registry.mappers
            if mapper.class_.__module__.startswith("mihomes.models")
        }

    def test_every_model_is_classified(self):
        """Fail closed on an unclassified model — including one added after this was written.

        This is the F.3b pattern applied inside a step: the gate enumerates the *code* rather
        than a transcription of it, so a new model cannot arrive unclassified and unnoticed.
        """
        unclassified = sorted(
            m.__name__ for m in self._application_models() if m not in ENTITY_CLASSES
        )
        assert not unclassified, (
            "every mapped model must land in exactly one §4.1 entity class (N4); "
            f"unclassified: {unclassified}"
        )

    def test_classification_names_no_model_that_does_not_exist(self):
        """The reverse direction — a stale entry for a deleted model hides a real gap.

        Without this, removing a model would leave its entry behind and the forward test would
        still pass at the same count, so the table would drift out of the schema unnoticed.
        """
        mapped = self._application_models()
        stale = sorted(m.__name__ for m in ENTITY_CLASSES if m not in mapped)
        assert not stale, f"ENTITY_CLASSES names unmapped classes: {stale}"

    def test_the_classification_actually_covers_the_whole_schema(self):
        """A guard on the guard: the module filter must not be silently excluding real models.

        If a future refactor moved application models out of `mihomes.models`, the filter above
        would quietly shrink the gate's scope to almost nothing and every classification test
        would still pass. Pinning the count makes that visible — the number is expected to change
        when a model is added, and changing it is a deliberate act.
        """
        assert len(self._application_models()) >= 42, (
            "the application-model filter is excluding models it should cover — "
            "check that models still live under mihomes.models"
        )

    def test_class_is_not_inferred_from_property_id(self):
        """C10 — §4.1's stated rationale is wrong even where its outcome is right.

        `Budget`, `Contract`, and `RecurringExpense` are account-level *by policy* (row 9 denies
        staff finances), not because they lack a property to scope by — all three carry
        `property_id`. Pinning this stops a future refactor from "correcting" them to
        property-scoped on the strength of the column, which would hand staff the finances.
        """
        from mihomes.models.budget import Budget
        from mihomes.models.contract import Contract
        from mihomes.models.recurring_expense import RecurringExpense

        for model in (Budget, Contract, RecurringExpense):
            assert hasattr(model, "property_id"), (
                f"{model.__name__} is expected to carry property_id — if it no longer does, "
                "C10's correction needs revisiting"
            )
            assert ENTITY_CLASSES[model] is EntityClass.ACCOUNT_LEVEL

    @pytest.mark.parametrize(
        "model_name, expected",
        [
            ("Vendor", EntityClass.PROPERTY_LINKED),
            ("Document", EntityClass.FLAGGED),
            ("Staff", EntityClass.PERSONNEL),
            ("User", EntityClass.GLOBAL),
            ("Task", EntityClass.PROPERTY_SCOPED),
        ],
    )
    def test_spec_named_classifications_are_preserved(self, model_name, expected):
        """The rows §4.1 *does* state must survive the extension to 42 models."""
        from mihomes.models import Base

        by_name = {m.class_.__name__: m.class_ for m in Base.registry.mappers}
        assert ENTITY_CLASSES[by_name[model_name]] is expected
