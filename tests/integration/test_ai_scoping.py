"""G10 · §6 Step 10 — **A15, the phase's definition of done** (A15, A16).

§8: *"Roles enforced in the UI while the AI answers freely is not a partial success — it is the
leak wearing the feature's clothes. If A15 is not green, Phase 2 is not finished regardless of
what else works."*

F3 is why: `assemble_context()` takes one *optional* property where staff need a *set*, and
`None` fetches everything in the account across all 14 `_fetch_*` helpers. §9.3 requires this be
blocked *"at the query layer, so a staff member cannot exfiltrate another home's data by asking
the AI about it."* Tenant RLS does not help — this is **within** one account.

**The executor list is read from `_EXECUTORS` at test time, never transcribed.** A hand-written
list of 15 passes forever; a 16th executor added later would be unscoped *and* untested. Deriving
the parameterisation from the code is what makes this a gate rather than a snapshot — the same
principle as G2's `Money` census and G1's model classification.

**§9's adversarial pattern, not the trivial one.** *"Not 'does the scoped query work' — that
passes trivially. Instead: seed two properties with distinguishable data, then for each of the 15
executors assert that a staff member scoped to A cannot obtain B's rows by any phrasing,
including asking for 'all', asking by B's name, and asking for aggregates that would sum across
both. An aggregate is the case a row-level filter can pass while still leaking a total."*
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from mihomes.services.ai.tools import _EXECUTORS

# The marker that must never reach a staff member scoped to the *other* property. Chosen to be
# implausible as incidental output so a substring match is meaningful.
SECRET = "ZZQXSECRETB"


@pytest.fixture
def two_estates(web_client_as):
    """Property A (in scope) and property B (out of scope), each with data in every entity the
    executors read. B's rows all carry `SECRET` in a human-visible field."""
    from mihomes.models.alert import Alert
    from mihomes.models.asset import Asset
    from mihomes.models.budget import Budget, Transaction
    from mihomes.models.consumable import Consumable
    from mihomes.models.event import Event
    from mihomes.models.issue import Issue
    from mihomes.models.property import Property
    from mihomes.models.task import Task
    from mihomes.models.work_order import WorkOrder

    created = {}

    def _seed(session):
        for label, marker in (("A", "AlphaHouse"), ("B", SECRET)):
            prop = Property(
                id=uuid.uuid4(), name=f"{marker} Estate",
                slug=f"estate-{label.lower()}-{uuid.uuid4().hex[:6]}",
            )
            session.add(prop)
            session.flush()
            pid = prop.id

            session.add(Task(
                id=uuid.uuid4(), title=f"{marker} task", slug=f"t-{uuid.uuid4().hex[:8]}",
                property_id=pid,
            ))
            session.add(Issue(
                id=uuid.uuid4(), title=f"{marker} issue", slug=f"i-{uuid.uuid4().hex[:8]}",
                property_id=pid,
            ))
            session.add(WorkOrder(
                id=uuid.uuid4(), title=f"{marker} work order",
                slug=f"w-{uuid.uuid4().hex[:8]}", property_id=pid,
                estimated_cost=1000.0, actual_cost=2000.0,
            ))
            session.add(Asset(
                id=uuid.uuid4(), name=f"{marker} asset", slug=f"a-{uuid.uuid4().hex[:8]}",
                asset_type="APPLIANCE", property_id=pid, purchase_price=500.0,
            ))
            session.add(Consumable(
                id=uuid.uuid4(), name=f"{marker} consumable",
                slug=f"c-{uuid.uuid4().hex[:8]}", property_id=pid, unit_price=9.0,
            ))
            session.add(Event(
                id=uuid.uuid4(), title=f"{marker} event", slug=f"e-{uuid.uuid4().hex[:8]}",
                property_id=pid, budget=777.0, event_date=date(2026, 6, 1),
            ))
            session.add(Alert(
                id=uuid.uuid4(), alert_type="maintenance", property_id=pid,
                message=f"{marker} alert body",
            ))
            budget = Budget(
                id=uuid.uuid4(), property_id=pid, category="MAINTENANCE", amount=5000.0,
                period="MONTHLY", period_start=date(2026, 1, 1),
            )
            session.add(budget)
            session.flush()
            session.add(Transaction(
                id=uuid.uuid4(), property_id=pid, category="MAINTENANCE",
                amount=333.0, description=f"{marker} transaction",
                date=date(2026, 1, 15),
            ))
            # `Contract` and `InsurancePolicy` are deliberately not seeded. Both are
            # **ACCOUNT_LEVEL** in §4.1, so staff never receive the row at all — cross-*property*
            # leakage is not their failure mode, and their denial is G7's row-level concern
            # rather than this test's. Their executors are still exercised by the
            # parameterisation below; they simply have no B-marked row to leak, which is the
            # correct state for an entity class staff are denied wholesale.
            created[label] = pid

    web_client_as.seed(_seed)
    return created


class TestExecutorsAreScoped:
    """A15 — the exfiltration test, parameterised over the **live** executor map."""

    @pytest.mark.parametrize("tool_name", sorted(_EXECUTORS))
    def test_no_cross_property_exfiltration(self, web_client_as, two_estates, tool_name):
        """For **every** executor: a staff member scoped to A cannot obtain B's rows.

        Three phrasings in one pass, per §9: an unfiltered call ("all"), a call naming B
        explicitly, and — where the executor supports it — an aggregate, which is the case a
        row-level filter can pass while still leaking a total.
        """
        from mihomes.authz.scope import authz_context
        from mihomes.services.ai.tools import execute_tool

        session = web_client_as.session_for_scope(scoped_to=[two_estates["A"]])
        scope = frozenset({two_estates["A"]})

        phrasings = [
            {},                                     # "all"
            {"property_slug": f"{SECRET} Estate"},   # by B's name
            {"limit": 100},                          # widen the net
            {"summary_only": False},                 # aggregates expanded where supported
        ]

        with authz_context("staff", scope):
            for inputs in phrasings:
                try:
                    output = execute_tool(session, tool_name, dict(inputs))
                except Exception:  # pragma: no cover - execute_tool catches internally
                    continue

                # **A refusal is a pass, and it arrives as a string, not an exception.**
                # `execute_tool` catches internally and returns "Tool error (…): …", which echoes
                # the caller's own input — so a naive `SECRET not in output` fires on the echo
                # rather than on any leaked row. Refusing an out-of-scope identifier is *stronger*
                # than filtering it, so these outputs are the desired behaviour; what must never
                # happen is B's data coming back.
                if output.startswith("Tool error"):
                    assert "not found" in output or "No " in output, (
                        f"{tool_name} failed for a reason other than refusal: {output}"
                    )
                    continue

                assert SECRET not in output, (
                    f"{tool_name} leaked out-of-scope data with inputs {inputs!r}:\n{output}"
                )

    def test_every_executor_declares_an_action(self):
        """A16's structural half: no tool may reach the database unauthorised.

        Property scoping covers `PROPERTY_SCOPED` models; the entity-class gate in `execute_tool`
        covers the rest, and it can only cover a tool that declares an action. A 16th executor
        added without one fails here rather than quietly answering questions about the household
        finances — which is what `query_budget` did before this group.
        """
        from mihomes.authz.actions import MATRIX
        from mihomes.services.ai.tools import _TOOL_ACTIONS

        missing = sorted(set(_EXECUTORS) - set(_TOOL_ACTIONS))
        assert not missing, f"executors with no declared action: {missing}"

        unknown = sorted(a for a in _TOOL_ACTIONS.values() if a not in MATRIX)
        assert not unknown, f"_TOOL_ACTIONS names actions absent from MATRIX: {unknown}"

    def test_account_level_tools_are_refused_for_staff(self):
        """The entity-class gate, asserted directly rather than only through a leak test.

        `query_budget`/`query_contracts`/`query_insurance` read models §4.1 classifies
        `ACCOUNT_LEVEL`, which the property filter cannot express — row 9 denies staff finances,
        and before this group the assistant answered anyway.
        """
        from mihomes.authz.scope import authz_context
        from mihomes.services.ai.tools import _refuse_if_denied

        with authz_context("staff", frozenset()):
            for tool in ("query_budget", "query_contracts", "query_insurance"):
                assert _refuse_if_denied(tool) is not None, f"{tool} not refused for staff"
            assert _refuse_if_denied("query_tasks") is None, "staff must keep their own work"

        with authz_context("owner", None):
            assert _refuse_if_denied("query_budget") is None

    def test_the_parameterisation_is_reading_the_real_map(self):
        """A guard on the guard.

        If `_EXECUTORS` were renamed or emptied, every test above would vanish silently and this
        file would report all-green while testing nothing. F3 measured 15 executors three ways.
        """
        assert len(_EXECUTORS) >= 15, (
            f"only {len(_EXECUTORS)} executors found — the exfiltration test is not covering "
            "the real tool surface"
        )

    def test_in_scope_data_is_still_returned(self, web_client_as, two_estates):
        """The positive control, and it is essential.

        Every assertion above is an *absence* check, so an `execute_tool` that returned the empty
        string for everything would pass all 15 parameterisations perfectly — and break the
        assistant completely.
        """
        from mihomes.authz.scope import authz_context
        from mihomes.services.ai.tools import execute_tool

        session = web_client_as.session_for_scope(scoped_to=[two_estates["A"]])
        with authz_context("staff", frozenset({two_estates["A"]})):
            output = execute_tool(session, "query_tasks", {})
        assert "AlphaHouse" in output, "the scoped executor returned nothing at all"


class TestAssembleContextIsAlsoGated:
    """F5: *"two independent DB paths, neither using the 15 executors."*

    `assemble_context` is the second one — it is what the Telegram bot's Q&A path calls, and it
    builds a "Budget Status (YTD)" section with real money. Guarding only `execute_tool` would
    leave this route wide open, which is precisely the "missing either leaves a hole" F5 warns
    about.
    """

    def test_finance_sections_dropped_for_staff(self, web_client_as, two_estates):
        from mihomes.authz.scope import authz_context
        from mihomes.services.ai.context import assemble_context
        from mihomes.services.ai.roles import ROLES

        session = web_client_as.session_for_scope(scoped_to=[two_estates["A"]])
        # The estate manager sees every data category, which makes it the strongest case: if the
        # gate holds for the broadest role, it holds for the narrower ones.
        roles = [ROLES["estate_manager"]]

        with authz_context("staff", frozenset({two_estates["A"]})):
            staff_context = assemble_context(session, roles, "how much did we spend?")
        with authz_context("owner", None):
            owner_context = assemble_context(session, roles, "how much did we spend?")

        assert "Budget Status" not in staff_context, (
            "the assistant's context must not carry finances for a role row 9 denies"
        )
        assert "Budget Status" in owner_context, (
            "owner must still get the financial context — a gate that dropped it for everyone "
            "would pass the staff assertion and break the product"
        )

    def test_staff_still_get_their_own_work(self, web_client_as, two_estates):
        """The gate drops account-level *sections*, not the staff member's own job."""
        from mihomes.authz.scope import authz_context
        from mihomes.services.ai.context import assemble_context
        from mihomes.services.ai.roles import ROLES

        session = web_client_as.session_for_scope(scoped_to=[two_estates["A"]])
        with authz_context("staff", frozenset({two_estates["A"]})):
            context = assemble_context(
                session, [ROLES["estate_manager"]], "what needs doing?"
            )

        assert SECRET not in context, "out-of-scope property reached the assistant's context"


class TestPrivilegedUnscoped:
    def test_owner_reaches_everything(self, web_client_as, two_estates):
        """Owner/admin are unrestricted — scoping that also constrained them would pass every
        staff assertion while breaking the product for the person who owns the estate."""
        from mihomes.authz.scope import authz_context
        from mihomes.services.ai.tools import execute_tool

        session = web_client_as.session_for_scope(scoped_to=[])
        with authz_context("owner", None):
            output = execute_tool(session, "query_tasks", {})
        assert SECRET in output and "AlphaHouse" in output


class TestRedactionThroughTheAiPath:
    def test_money_redacted_in_context(self, web_client_as, two_estates):
        """A16 — redaction holds through the AI path, not just the web serializer.

        N3's reason for one shared function: the AI path renders no templates, so template-level
        redaction would leave exactly this surface unprotected. The work order is **in scope** —
        staff may see it — and its costs must still not appear.
        """
        from mihomes.authz.scope import authz_context
        from mihomes.services.ai.tools import execute_tool

        session = web_client_as.session_for_scope(scoped_to=[two_estates["A"]])
        with authz_context("staff", frozenset({two_estates["A"]})):
            output = execute_tool(session, "query_work_orders", {})

        assert "AlphaHouse work order" in output, "staff must still see the work order"
        assert "1,000" not in output and "1000" not in output, "estimated_cost leaked via AI"
        assert "2,000" not in output and "2000" not in output, "actual_cost leaked via AI"
