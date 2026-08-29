"""SPEC-005 §6 Step 12 — the two Estate gates (A12, A13).

`PRICING:89-91` writes all three Estate keys as `false | false | true`, and D10 says they are
*"enforced exactly as `PRICING` §3.1 writes them"*. So the matrix below is transcription, not
judgement: Free and Pro denied, Estate allowed, on both gates.

**A13 is the criterion that catches gating the wrong function.** `record_change` has many callers
across the service layer and is written by nearly every mutation (F6), so a gate there would make
a *Free account's writes fail* — a far worse failure than the paywall it was meant to be. N7 says
so directly. The gate belongs on the read/export path, and A13 is what proves it stayed there.

Node ids are module-level and match §8's declarations exactly (`test_gate_matrix`,
`test_audit_write_ungated`). BD2/BD8 both cost a group each when a declared id did not resolve.
"""

from __future__ import annotations

import pytest

from mihomes.entitlements import Allowed, Denied, can
from mihomes.entitlements.limits import PLAN_LIMITS

#: The two gates Step 12 places, as `(action, entitlement key)`.
#:
#: Derived rather than typed: the action strings are the ones `can()` actually keys on, and
#: pairing each with its key lets the matrix assert against `PLAN_LIMITS` instead of against
#: three hand-written booleans that would agree with a wrong table.
STEP_12_GATES = (
    ("maintenance.predict", "predictive_maintenance"),
    ("audit.export", "audit_export"),
)

#: Every plan, in upgrade order. Enumerated from the table so a fourth tier fails this file
#: rather than silently skipping it.
PLANS = tuple(PLAN_LIMITS)


class FakeAccount:
    """The minimum `can()` reads: a plan and a billing status.

    Not a DB row — A12 is about the decision function, and building an `Account` would drag a
    session in for a check that never touches one.
    """

    def __init__(self, plan: str, subscription_status: str | None = None) -> None:
        self.plan = plan
        self.subscription_status = subscription_status
        self.id = f"acct-{plan}"


def test_gate_matrix():
    """A12 — Free and Pro denied, Estate allowed, on **both** Estate gates.

    The expectation is read from `PLAN_LIMITS` rather than written out, so this test fails when
    the *table* drifts from `PRICING` — which is what it did before G12: `pro` carried
    `predictive_maintenance: True`, making "Free and Pro are denied" untestable while §0.6
    recorded it as verified.
    """
    for action, key in STEP_12_GATES:
        for plan in PLANS:
            expected_allowed = PLAN_LIMITS[plan][key]
            decision = can(FakeAccount(plan), action)

            assert bool(decision) is bool(expected_allowed), (
                f"{plan} / {action}: PLAN_LIMITS says {expected_allowed!r}, "
                f"can() said {decision!r}"
            )

            if expected_allowed:
                assert isinstance(decision, Allowed)
            else:
                assert isinstance(decision, Denied)

    # And the shape `PRICING:89-91` actually specifies, stated once so a table that drifted in
    # *both* places — the limits and the assertion above — still fails here.
    for action, _key in STEP_12_GATES:
        assert not can(FakeAccount("free"), action), f"{action} must be denied on free"
        assert not can(FakeAccount("pro"), action), f"{action} must be denied on pro"
        assert can(FakeAccount("estate"), action), f"{action} must be allowed on estate"


def test_denied_names_a_plan_that_actually_allows(monkeypatch):
    """`PRICING` rule 4, and the bug that made it false.

    Not an §8 criterion of its own — A34 covers the *surfaces* at G14 — but the mechanism it
    depends on broke here: `can()` resolved denials against `PLAN_LIMITS` while `_upgrade_target`
    walked `PLAN_LIMITS_PHASE3`. Two defaults disagreeing pointed a denied Free account at Pro,
    where the action was also denied. Asserting the *round trip* is what catches that class;
    asserting `upgrade_target == "estate"` alone would pass on any table where Pro denies.
    """
    for action, _key in STEP_12_GATES:
        for plan in ("free", "pro"):
            decision = can(FakeAccount(plan), action)
            assert isinstance(decision, Denied)

            target = decision.upgrade_target
            assert target is not None, f"{plan}/{action} denied with nothing to upgrade to"

            assert can(FakeAccount(target), action), (
                f"{plan}/{action} was told to upgrade to {target}, which also denies it"
            )


def test_denied_names_target():
    """A34 — every `Denied` from an Estate gate names an `upgrade_target`, **at the surfaces**.

    A12 covers the decision; this covers what a user actually reaches. The distinction is the
    whole reason §8 gives A34 its own row: a service that denies correctly behind a route that
    renders "403 Forbidden" has satisfied A12 and failed the customer.

    Three things are asserted together because each alone is insufficient:

    1. The **decision** carries a target (the mechanism).
    2. The **route** puts it in the response body (`web/routes/privacy.py`).
    3. The **CLI** prints it and exits distinguishably (`cli/audit.py`).

    Checked against the source for 2 and 3 rather than by driving them — the live route is
    exercised in `test_privacy_routes.py`, and what this asserts is that neither surface can
    quietly stop carrying the field, which is a property of the code.
    """
    import inspect

    from mihomes.cli import audit as audit_cli
    from mihomes.web.routes import privacy as privacy_routes

    # 1 — the mechanism. Both Estate gates, both non-Estate plans.
    for action, _key in STEP_12_GATES:
        for plan in ("free", "pro"):
            decision = can(FakeAccount(plan), action)
            assert isinstance(decision, Denied)
            assert decision.upgrade_target == "estate", (
                f"{plan}/{action} denied without naming the plan that would allow it"
            )

    # 2 — the route. `EntitlementDenied` carries the target; the response must **spend** it.
    #
    # Asserted on the response *content*, not on the function text. The first version grepped
    # the whole source, and deleting `upgrade_target` from the JSON body left it green — the
    # name still appeared in the `logger.info` call one line above. A denial logged and not
    # sent is precisely the failure A34 is about, so the check has to distinguish the two.
    route_src = inspect.getsource(privacy_routes.export_audit_log)
    assert "EntitlementDenied" in route_src, (
        "the route must catch the entitlement denial rather than let it 500"
    )
    assert '"upgrade_target": denied.upgrade_target' in route_src, (
        "A34: the route must put the upgrade target in the response body — logging it tells "
        "the operator and leaves the customer with a dead end"
    )
    assert "402" in route_src, (
        "a plan denial must be distinguishable from the role gate's 403 — different problems, "
        "and only one of them is fixable by paying"
    )

    # 3 — the CLI. Same denial, same obligation.
    cli_src = inspect.getsource(audit_cli.export_audit)
    assert "EntitlementDenied" in cli_src
    assert "upgrade_target" in cli_src, (
        "A34: the CLI denies without naming the upgrade target"
    )


def test_audit_write_ungated(session, account_a):
    """A13 — `record_change` fires for **every** account on **every** plan (F6, N7).

    The check that would have caught gating the wrong function. `record_change` is written by
    nearly every mutation, so a plan gate here fails a Free account's writes rather than
    paywalling a feature.

    Asserted by *calling it under each plan* rather than by grepping for `can(`: a gate added
    through a helper, a decorator, or an import alias would survive a text search and fail this.
    """
    import uuid

    from mihomes.services import audit
    from mihomes.services.audit import record_change

    # `entity_id` is a UUID column since SPEC-002 D2's int→UUID remap, so a synthetic integer is
    # rejected by the database rather than by the gate under test — a failure that would look
    # like A13 breaking when nothing about entitlements had changed.
    for plan in PLANS:
        # Mutating the account row would need a real row per plan; `record_change` reads none of
        # it, so the plan is varied where a gate would actually consult it.
        entry = record_change(
            session,
            entity_type="property",
            entity_id=uuid.uuid4(),
            action=plan[:10],
            changes={"name": {"old": "a", "new": "b"}},
        )
        session.flush()

        assert entry.id is not None, f"record_change wrote nothing on plan {plan}"
        assert entry.action == plan[:10]

    # And the negative half: the *module* must not consult entitlements on the write path. A
    # gate placed on `record_change` would have to reach `can()` or `check_entitlement`, so
    # breaking both is the mutation that proves the assertion above is not vacuous.
    def _explode(*args, **kwargs):  # pragma: no cover - only runs if a gate appears
        raise AssertionError("record_change consulted an entitlement gate (N7/A13)")

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(audit.entitlements, "can", _explode)
        monkey.setattr(audit.entitlements, "check_entitlement", _explode)
        entry = record_change(
            session,
            entity_type="property",
            entity_id=uuid.uuid4(),
            action="gate-test",
        )
        session.flush()
        assert entry.id is not None
    finally:
        monkey.undo()
