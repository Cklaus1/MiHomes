"""G2 · §6 Step 2 — the price map (A29).

**A29 is a structural assertion, not a behavioural one**, and that is what makes it worth
writing: *"No price id appears in any signature or arrives from a request."* A test that merely
checks `price_id_for("pro", "monthly")` returns the right string would still pass in a world
where `create_checkout_session` also accepted a `price_id` parameter — which is exactly the
defect D3/N2 exist to prevent, since a client-supplied price id is a self-service discount.

So `test_no_price_id_in_interface` inspects the **signatures**, and it derives the set of
functions to inspect from the Protocol rather than listing them, so a seventh method added later
is checked without anyone remembering to add it here.
"""

from __future__ import annotations

import inspect

import pytest

from mihomes.services.billing.prices import (
    PRICE_ENV_VARS,
    PriceConfigurationError,
    plan_for_price_id,
    price_id_for,
)
from mihomes.services.billing.provider import BillingProvider


class TestEnvResolution:
    def test_resolves_from_env(self, monkeypatch):
        monkeypatch.setenv("STRIPE_PRICE_PRO_MONTHLY", "price_abc123")
        assert price_id_for("pro", "monthly") == "price_abc123"

    def test_missing_env_names_the_var(self, monkeypatch):
        """G2.1 — the failure message must name the variable to set.

        Following `ai_config.py`'s precedent: a misconfiguration should be self-diagnosing where
        it fails, not three frames away in a Stripe SDK error about a null price.
        """
        monkeypatch.delenv("STRIPE_PRICE_ESTATE_ANNUAL", raising=False)
        with pytest.raises(PriceConfigurationError) as exc:
            price_id_for("estate", "annual")
        assert "STRIPE_PRICE_ESTATE_ANNUAL" in str(exc.value)

    def test_unknown_plan_lists_what_is_supported(self):
        with pytest.raises(PriceConfigurationError) as exc:
            price_id_for("platinum", "monthly")
        assert "platinum" in str(exc.value)
        assert "pro/monthly" in str(exc.value)

    def test_free_has_no_price(self):
        """**D4** — *"Free accounts have no Stripe subscription object."*

        Stripe objects are created at conversion only, so a `STRIPE_PRICE_FREE_*` would name
        something that must never exist. Asserting the absence is what stops someone "completing"
        the map later by adding one.
        """
        assert not any(plan == "free" for plan, _ in PRICE_ENV_VARS)
        with pytest.raises(PriceConfigurationError):
            price_id_for("free", "monthly")

    def test_four_combinations_exactly(self):
        assert set(PRICE_ENV_VARS) == {
            ("pro", "monthly"), ("pro", "annual"),
            ("estate", "monthly"), ("estate", "annual"),
        }


class TestInverse:
    def test_price_id_maps_back_to_its_plan(self, monkeypatch):
        monkeypatch.setenv("STRIPE_PRICE_ESTATE_MONTHLY", "price_est_m")
        assert plan_for_price_id("price_est_m") == "estate"

    def test_unknown_price_id_is_none_not_free(self, monkeypatch):
        """**The sharp one.** An unrecognised price id must not resolve to `"free"`.

        Defaulting here would silently downgrade a paying customer: they bought something this
        deployment cannot name — a price created in the dashboard but never wired to an env var —
        and recording that as Free strips the entitlements they are being charged for. `None`
        hands the uncertainty to the caller, which is the only party that can decide.
        """
        monkeypatch.delenv("STRIPE_PRICE_PRO_MONTHLY", raising=False)
        monkeypatch.delenv("STRIPE_PRICE_PRO_ANNUAL", raising=False)
        monkeypatch.delenv("STRIPE_PRICE_ESTATE_MONTHLY", raising=False)
        monkeypatch.delenv("STRIPE_PRICE_ESTATE_ANNUAL", raising=False)
        assert plan_for_price_id("price_who_knows") is None

    def test_the_two_directions_agree(self, monkeypatch):
        """Round-trip every configured combination.

        This is the drift check `BILLING` §4.2 asks for by keeping both maps in one module: if
        the forward and reverse directions ever disagree, a customer's purchase and the plan we
        record for them diverge, and nothing else in the system would notice.
        """
        for (plan, interval), env_var in PRICE_ENV_VARS.items():
            monkeypatch.setenv(env_var, f"price_{plan}_{interval}")
        for (plan, interval) in PRICE_ENV_VARS:
            assert plan_for_price_id(price_id_for(plan, interval)) == plan


class TestNoPriceIdInInterface:
    def test_no_price_id_in_interface(self):
        """**A29** — no `BillingProvider` method takes a price id (D3/N2).

        Derived from the Protocol rather than a hand-listed set of methods, so a method added in
        a later phase is covered without anyone remembering this test exists — the same
        derive-from-the-code principle A11 is built on.
        """
        offenders = []
        for name in dir(BillingProvider):
            if name.startswith("_"):
                continue
            member = getattr(BillingProvider, name, None)
            if not callable(member):
                continue
            for param in inspect.signature(member).parameters:
                if "price" in param.lower():
                    offenders.append(f"{name}({param})")

        assert not offenders, (
            "a vendor price id must never appear in the interface (D3/N2) — a client-supplied "
            f"price id is a self-service discount. Found: {offenders}"
        )

    def test_checkout_takes_plan_and_interval(self):
        """The positive half: the parameters that *replace* a price id are actually there."""
        params = inspect.signature(BillingProvider.create_checkout_session).parameters
        assert "plan" in params
        assert "interval" in params
