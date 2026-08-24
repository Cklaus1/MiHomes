"""G6 · §6 Step 6 — checkout, portal, plan page (A28).

**A28 is "a non-owner is denied every billing route", and the plural is the assertion.** Testing
one route proves that route; the risk is the *next* one added without a declaration, or added with
the wrong access class. So the denial test enumerates the billing routes from the mounted app at
test time rather than listing them here — the same derive-from-the-code principle A11 rests on.

D8's reasoning is worth keeping in view while reading these: *"admins manage the estate, not the
card."* An admin running the household is a different trust boundary from the payment method, so
`billing.manage` (row 15) is owner-only and **admin is denied here just as staff is** — which is
unusual enough in this codebase that a test asserting only staff-denial would look complete and
miss half the rule.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from mihomes.models.account import Account
from mihomes.services.billing.provider import SubscriptionState
from mihomes.services.billing.service import start_checkout, start_portal_session


class FakeProvider:
    """Records what it was asked for. Structural satisfaction, no subclassing (§9)."""

    def __init__(self) -> None:
        self.created_customers: list[tuple[str, str, str]] = []
        self.checkout_calls: list[dict] = []
        self.portal_calls: list[dict] = []

    def create_customer(self, *, account_id: str, email: str, name: str) -> str:
        self.created_customers.append((account_id, email, name))
        return f"cus_fake_{len(self.created_customers)}"

    def create_checkout_session(self, *, customer_id, plan, interval,
                               success_url, cancel_url) -> str:
        self.checkout_calls.append({
            "customer_id": customer_id, "plan": plan, "interval": interval,
            "success_url": success_url, "cancel_url": cancel_url,
        })
        return f"https://checkout.example/{plan}/{interval}"

    def create_portal_session(self, *, customer_id, return_url) -> str:
        self.portal_calls.append({"customer_id": customer_id, "return_url": return_url})
        return f"https://portal.example/{customer_id}"

    def get_subscription(self, *, customer_id) -> SubscriptionState:  # pragma: no cover
        return SubscriptionState(None, None, None, None, False)

    def cancel(self, *, subscription_id, at_period_end=True) -> None:  # pragma: no cover
        return None

    def handle_webhook_event(self, *, payload, signature):  # pragma: no cover
        return None


@pytest.fixture
def billable_account(session, account_a) -> Account:
    """An account with an **active owner membership**, which is what makes it billable.

    The bare `account_a` fixture creates the account row and nothing else, so
    `_billing_email` — which resolves the owner's address for receipts and dunning — finds
    nobody. That is the correct behaviour on a real account (every account has an owner by
    SPEC-002's partial unique index, so its absence signals corruption) and a fixture gap here,
    so the fixture supplies what production guarantees.
    """
    import uuid as _uuid

    from mihomes.ids import new_id
    from mihomes.models.membership import Membership
    from mihomes.models.user import User

    user = User(
        id=new_id(),
        google_sub=f"sub-billing-{_uuid.uuid4().hex[:8]}",
        email="owner@example.com",
        name="Billing Owner",
    )
    session.add(user)
    session.flush()
    session.add(
        Membership(
            id=new_id(),
            account_id=account_a,
            user_id=user.id,
            role="owner",
            status="active",
        )
    )
    session.commit()
    return session.get(Account, account_a)


def _billing_routes() -> list[str]:
    """Every mounted billing route path, read from the app.

    Derived rather than listed so a seventh route added later is covered by A28 without anyone
    remembering this file exists.
    """
    from mihomes.web.app import create_app

    return sorted({
        r.path for r in create_app().routes
        if isinstance(r, APIRoute) and r.path.startswith("/billing")
    })


class TestOwnerOnly:
    @pytest.mark.parametrize("role", ["admin", "staff"])
    def test_owner_only(self, web_client_as, role):
        """**A28** — every billing route refuses a non-owner, **admin included**.

        The admin half is the one that could be missed: everywhere else in this app an admin is
        the near-equal of an owner, and a test covering only staff would read as complete. D8 is
        explicit that billing is the exception.
        """
        client = web_client_as(role)
        for path in _billing_routes():
            get = client.get(path)
            assert get.status_code in (403, 405), (
                f"{role} reached GET {path} (status {get.status_code}) — billing is owner-only "
                "(D8, row 15)"
            )
            post = client.post(path, data={"plan": "pro", "interval": "monthly"})
            assert post.status_code in (403, 405), (
                f"{role} reached POST {path} (status {post.status_code}) — billing is owner-only"
            )

    def test_owner_reaches_the_plan_page(self, web_client_as):
        """The positive control. Without it, a route that 403'd *everyone* would pass A28 —
        the classic vacuous authorization test."""
        response = web_client_as("owner").get("/billing")
        assert response.status_code == 200

    def test_every_billing_route_declares_the_billing_action(self):
        """The gate behind the gate.

        A28 tests behaviour through the client; this asserts the *declaration*, because
        enforcement is app-wide and reads exactly this attribute. A billing route declaring some
        other action would still be enforced — just against the wrong rule, and the client test
        would only notice if that rule happened to differ for the role it tried.
        """
        from mihomes.authz.declare import declared_action
        from mihomes.web.app import create_app

        wrong = []
        for route in create_app().routes:
            if isinstance(route, APIRoute) and route.path.startswith("/billing"):
                declared = declared_action(route.endpoint)
                if declared is None or declared[0] != "billing.manage":
                    wrong.append(f"{route.path}: {declared}")

        assert not wrong, f"billing routes must declare billing.manage (row 15): {wrong}"


class TestCheckout:
    def test_customer_reused(self, session, billable_account):
        """A returning customer reuses `stripe_customer_id` — no second Stripe Customer.

        Not cosmetic. The webhook maps `provider_customer_id -> account`, so a second Customer's
        events resolve to nothing: the upgrade is paid for and **silently never applies**, which
        looks to the customer like the product taking their money and doing nothing.
        """
        account = billable_account
        provider = FakeProvider()

        start_checkout(
            session, account, plan="pro", interval="monthly",
            success_url="http://localhost/billing/success",
            cancel_url="http://localhost/billing",
            provider=provider,
        )
        first_customer = account.stripe_customer_id

        start_checkout(
            session, account, plan="estate", interval="annual",
            success_url="http://localhost/billing/success",
            cancel_url="http://localhost/billing",
            provider=provider,
        )

        assert len(provider.created_customers) == 1, (
            "a second Stripe Customer orphans the webhook mapping — the upgrade would be paid "
            "for and never applied"
        )
        assert account.stripe_customer_id == first_customer

    def test_checkout_passes_plan_and_interval_not_a_price(self, session, billable_account):
        """D3/N2 at the call site. The interface has no price parameter, and this proves the
        service does not smuggle one in some other field."""
        account = billable_account
        provider = FakeProvider()

        start_checkout(
            session, account, plan="pro", interval="annual",
            success_url="http://localhost/billing/success",
            cancel_url="http://localhost/billing",
            provider=provider,
        )

        call = provider.checkout_calls[0]
        assert call["plan"] == "pro"
        assert call["interval"] == "annual"
        assert not any("price" in key.lower() for key in call)

    def test_customer_id_is_committed_before_checkout(self, session, billable_account):
        """The ordering, asserted rather than assumed.

        If checkout were created first and the commit then failed, Stripe would hold a Customer
        this database has never heard of — and its webhooks would land in the unmappable bucket
        forever. Committing first can at worst leave an id for an abandoned checkout, which the
        next attempt reuses.
        """
        account = billable_account
        provider = FakeProvider()

        start_checkout(
            session, account, plan="pro", interval="monthly",
            success_url="http://localhost/billing/success",
            cancel_url="http://localhost/billing",
            provider=provider,
        )

        session.expire_all()
        assert session.get(Account, billable_account.id).stripe_customer_id is not None

    def test_an_unsellable_plan_is_refused(self, web_client_as):
        """Server-side validation of the *plan name* — the client-supplied half of D3/N2.

        A price id can never arrive (no parameter accepts one), but a plan name can. `"free"` is
        the interesting case: it is a real plan with no Stripe object at all (D4), so an
        unvalidated request for it would reach the price map and fail confusingly rather than
        being refused as nonsense.
        """
        response = web_client_as("owner").post(
            "/billing/checkout", data={"plan": "free", "interval": "monthly"},
        )
        assert response.status_code == 400


class TestPortal:
    def test_portal_requires_a_customer(self, session, account_a):
        """Free accounts have no Stripe object (D4), so there is nothing to manage.

        Raising here rather than sending the user to a portal URL for a nonexistent customer,
        which would fail inside Stripe's own UI where this application can neither explain it nor
        recover from it.
        """
        from mihomes.services.billing.provider import BillingProviderError

        account = session.get(Account, account_a)
        account.stripe_customer_id = None

        with pytest.raises(BillingProviderError):
            start_portal_session(account, return_url="http://localhost/billing")

    def test_portal_uses_the_stored_customer(self, session, account_a):
        account = session.get(Account, account_a)
        account.stripe_customer_id = "cus_existing"
        provider = FakeProvider()

        start_portal_session(
            account, return_url="http://localhost/billing", provider=provider,
        )

        assert provider.portal_calls[0]["customer_id"] == "cus_existing"


class TestSuccessGrantsNothing:
    def test_success_page_does_not_change_the_plan(self, web_client_as, session, account_a):
        """**N1 — the defect that fails open**, and the most common Stripe integration bug.

        The user controls `success_url`: they can reach it without paying, replay it, or never
        arrive after a successful payment. Only a verified webhook changes state (D1). Asserted
        by hitting the page as a Free account and requiring the plan to be untouched.
        """
        before = session.get(Account, account_a).plan

        response = web_client_as("owner").get("/billing/success")
        assert response.status_code == 200

        session.expire_all()
        assert session.get(Account, account_a).plan == before, (
            "the success redirect must grant nothing — it is a confirmation page, and the user "
            "controls whether and when they reach it (N1/D1)"
        )
