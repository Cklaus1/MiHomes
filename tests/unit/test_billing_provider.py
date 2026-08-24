"""G1 · §6 Step 1 — the billing provider seam.

`BILLING` §4.1 already specifies the exceptions, the two frozen dataclasses and the Protocol.
SPEC-004 §5.1 is explicit that they are **reused verbatim** — *"do not redeclare, rename, or
'improve' them."* So these tests assert the declarations match the doc, not that they are
reasonable: a renamed field here is a divergence that every later step inherits.

**The structural-satisfaction test is the load-bearing one.** `BillingProvider` is a
`Protocol`, and §9's fixture plan requires `FakeBillingProvider` to satisfy it *without
subclassing* — the same precedent `AIProvider` set. If the Protocol is accidentally declared as
an ABC, or a method signature drifts, the fake stops matching and every downstream test in this
phase is testing a shape the real adapter does not have.

**N5 is what these tests defend:** *"Do not let the provider adapter touch the database."*
`NormalizedEvent` carries provider identifiers and no `account_id`, and the test below asserts
that absence directly — it is the one field whose presence would silently dissolve the seam.
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import UTC, datetime

import pytest

from mihomes.services.billing.provider import (
    BillingAuthError,
    BillingProvider,
    BillingProviderError,
    NormalizedEvent,
    SubscriptionState,
    WebhookVerificationError,
    get_billing_provider,
)


class FakeBillingProvider:
    """Satisfies `BillingProvider` structurally — no subclassing (§9, `AIProvider` precedent).

    Every test in this phase that is not specifically about parsing a real Stripe payload uses
    this. It is defined here rather than in `conftest.py` for now because G1 is the only consumer;
    it moves to a fixture at G5 when the webhook tests need a queue of events.
    """

    def __init__(self) -> None:
        self.created_customers: list[tuple[str, str, str]] = []
        self.state = SubscriptionState(
            provider_subscription_id=None,
            plan=None,
            status=None,
            current_period_end=None,
            cancel_at_period_end=False,
        )

    def create_customer(self, *, account_id: str, email: str, name: str) -> str:
        self.created_customers.append((account_id, email, name))
        return f"cus_fake_{len(self.created_customers)}"

    def create_checkout_session(self, *, customer_id: str, plan: str, interval: str,
                               success_url: str, cancel_url: str) -> str:
        return f"https://checkout.example/{customer_id}/{plan}/{interval}"

    def get_subscription(self, *, customer_id: str) -> SubscriptionState:
        return self.state

    def cancel(self, *, subscription_id: str, at_period_end: bool = True) -> None:
        return None

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        return f"https://portal.example/{customer_id}"

    def handle_webhook_event(self, *, payload: bytes, signature: str) -> NormalizedEvent | None:
        return None


class TestProtocolShape:
    def test_fake_satisfies_protocol_structurally(self):
        """G1.1 — a class that subclasses nothing still satisfies the Protocol.

        `isinstance` against a non-runtime-checkable Protocol raises, so this asserts the thing
        that actually matters and that a type-checker would catch: every method the Protocol
        declares exists on the fake, with a matching signature.
        """
        fake = FakeBillingProvider()
        assert FakeBillingProvider.__bases__ == (object,), (
            "the fake must satisfy the Protocol structurally, not by inheriting it — that is "
            "what proves BillingProvider is a Protocol and not an ABC (§9)"
        )

        protocol_methods = [
            name for name in dir(BillingProvider)
            if not name.startswith("_") and callable(getattr(BillingProvider, name, None))
        ]
        assert protocol_methods, "the Protocol declares no methods — it was not transcribed"

        for name in protocol_methods:
            assert hasattr(fake, name), f"FakeBillingProvider is missing {name}()"
            expected = inspect.signature(getattr(BillingProvider, name))
            actual = inspect.signature(getattr(fake, name))
            assert list(expected.parameters)[1:] == list(actual.parameters), (
                f"{name}() signature drifted from BILLING §4.1: "
                f"{list(expected.parameters)[1:]} != {list(actual.parameters)}"
            )

    def test_protocol_declares_the_six_methods_billing_names(self):
        """§4.1's method set, exactly. A missing one is a step that cannot be built later."""
        declared = {
            name for name in dir(BillingProvider)
            if not name.startswith("_") and callable(getattr(BillingProvider, name, None))
        }
        assert declared == {
            "create_customer", "create_checkout_session", "get_subscription",
            "cancel", "create_portal_session", "handle_webhook_event",
        }


class TestNormalizedEventKeepsTheSeam:
    def test_normalized_event_carries_no_account_id(self):
        """**N5/D2 — the seam itself.**

        *"`NormalizedEvent` deliberately carries provider ids, not an `account_id`. Mapping
        `provider_customer_id -> account` is `BillingService`'s job."* The moment this dataclass
        grows an `account_id`, the adapter needs a database to populate it and stops being
        swappable or testable. Asserting the field's **absence** is the only way to catch that,
        because adding it would break nothing else.
        """
        fields = {f.name for f in dataclasses.fields(NormalizedEvent)}
        assert "account_id" not in fields, (
            "NormalizedEvent must carry provider identifiers only (D2/N5) — an account_id here "
            "forces the adapter to touch the DB"
        )
        assert fields == {
            "type", "provider_customer_id", "subscription", "raw_event_id", "occurred_at",
        }

    def test_both_dataclasses_are_frozen(self):
        """`BILLING` §4.1 declares both `frozen=True`.

        An event that a handler can mutate mid-flight is a debugging problem in the one code
        path where replays and out-of-order delivery already make state hard to reason about.
        """
        assert NormalizedEvent.__dataclass_params__.frozen
        assert SubscriptionState.__dataclass_params__.frozen

    def test_subscription_state_fields_match_the_doc(self):
        fields = {f.name for f in dataclasses.fields(SubscriptionState)}
        assert fields == {
            "provider_subscription_id", "plan", "status",
            "current_period_end", "cancel_at_period_end",
        }

    def test_a_normalized_event_round_trips(self):
        """Constructible with the doc's own example shape — catches a type annotation that
        cannot actually hold what §6's events carry."""
        event = NormalizedEvent(
            type="subscription.activated",
            provider_customer_id="cus_123",
            subscription=SubscriptionState(
                provider_subscription_id="sub_123",
                plan="pro",
                status="active",
                current_period_end=datetime(2026, 9, 1, tzinfo=UTC),
                cancel_at_period_end=False,
            ),
            raw_event_id="evt_123",
            occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
        )
        assert event.subscription.plan == "pro"
        assert event.occurred_at.tzinfo is not None, (
            "occurred_at must be timezone-aware — §6's out-of-order comparison comes apart on "
            "naive datetimes, and the DB column is DateTime(timezone=True)"
        )


class TestExceptionHierarchy:
    def test_both_subclass_the_base(self):
        """One `except BillingProviderError` at the route boundary must catch everything the
        adapter raises. A sibling class would escape it and 500 the webhook."""
        assert issubclass(BillingAuthError, BillingProviderError)
        assert issubclass(WebhookVerificationError, BillingProviderError)


class TestFactory:
    def test_factory_returns_stripe(self, monkeypatch):
        """G1.3 — the `"stripe"` branch resolves and imports lazily.

        The key is injected via env rather than passed, which is the assertion hiding inside this
        test: `get_billing_provider` has no parameter to pass it through (§9, N12).
        """
        monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_test_notreal")
        provider = get_billing_provider("stripe")
        assert type(provider).__name__ == "StripeProvider"

    def test_missing_secret_key_raises_auth_error_naming_the_var(self, monkeypatch):
        """A missing key fails at **construction**, not at the first outbound call.

        Every method needs it, so failing here points the stack trace at configuration instead of
        at whichever feature happened to bill first. `BillingAuthError` and not a bare
        `KeyError`, so the one `except BillingProviderError` at the route boundary still catches
        it.
        """
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        with pytest.raises(BillingAuthError) as exc:
            get_billing_provider("stripe")
        assert "STRIPE_SECRET_KEY" in str(exc.value)

    def test_unknown_provider_names_supported_list(self):
        """G1.2 — mirrors `ai/provider.py`'s explicit `else: raise`, message included.

        The supported list in the message is what makes a typo self-diagnosing at the point of
        failure rather than three frames away.
        """
        with pytest.raises(BillingProviderError) as exc:
            get_billing_provider("paddle")
        assert "paddle" in str(exc.value)
        assert "stripe" in str(exc.value)

    def test_factory_takes_no_api_key(self):
        """§5.1 — *"Unlike `get_provider()`, takes no api_key"*.

        `BILLING` §9 requires the key to come from the environment, never from a caller and never
        from `configurations.value` (N12). A key parameter is how the second one happens by
        accident: a caller reaches for the nearest config value and the secret lands in the DB.
        """
        params = inspect.signature(get_billing_provider).parameters
        assert "api_key" not in params
        assert "secret_key" not in params
