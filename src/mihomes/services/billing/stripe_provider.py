"""`StripeProvider` — **the only Stripe-aware module in the tree** (D2).

Nothing else imports `stripe`. That is not tidiness: it is what makes `FakeBillingProvider`
sufficient for every criterion in §8, and therefore what lets this phase be proved without a live
Stripe account (§0.8 U2). A second import site would quietly make the fake a partial answer.

**Credentials come from the environment, never from a caller and never from
`configurations.value`** (`BILLING` §9, N12, SPEC-003 N11). The two secrets are checked at
different moments on purpose:

- `STRIPE_SECRET_KEY` is validated **at construction** — every outbound call needs it, so failing
  at the factory gives a stack trace pointing at configuration rather than at whichever feature
  happened to call first. Same shape as `ClaudeProvider`.
- `STRIPE_WEBHOOK_SECRET` is validated **at use**, in `handle_webhook_event`. It is needed by
  exactly one method, and a deployment doing checkout-only would otherwise be blocked at startup
  by a secret it never uses. The error names the variable either way.

**Excluded from coverage** (`pyproject.toml`), following the precedent set for the AI provider
HTTP implementations: testing that the Stripe SDK works is Stripe's job. The seam worth testing
is the Protocol boundary, and `FakeBillingProvider` tests it.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from mihomes.services.billing.provider import (
    BillingAuthError,
    NormalizedEvent,
    SubscriptionState,
    WebhookVerificationError,
)

__all__ = ["StripeProvider"]

logger = logging.getLogger(__name__)

SECRET_KEY_ENV = "STRIPE_SECRET_KEY"
WEBHOOK_SECRET_ENV = "STRIPE_WEBHOOK_SECRET"

#: Stripe event type → our vendor-neutral `NormalizedEvent.type` (`BILLING` §6).
#:
#: An event type absent from this map is **deliberately ignored**: `handle_webhook_event` returns
#: `None` and the route still acks 2xx. Stripe sends far more event types than a subscription
#: product cares about, and retrying the ones we ignore forever is the failure mode this avoids.
_EVENT_TYPE_MAP = {
    # `BILLING` §6's first row, and the one that links a Checkout to an account: it is the only
    # event carrying the `client_reference_id` we set when starting checkout, so a customer whose
    # `stripe_customer_id` is not yet stored is resolvable from it. Omitting it would leave the
    # very first upgrade unmappable — the exit criterion's own path.
    "checkout.session.completed": "checkout.completed",
    "customer.subscription.created": "subscription.activated",
    "customer.subscription.updated": "subscription.updated",
    "customer.subscription.deleted": "subscription.cancelled",
    "customer.subscription.trial_will_end": "subscription.trial_will_end",
    "invoice.paid": "invoice.paid",
    "invoice.payment_failed": "invoice.payment_failed",
}

#: Stripe's `Subscription.status` → the normalized set (`BILLING` §5's normalization rule).
#:
#: *"`incomplete`, `incomplete_expired`, and `paused` normalize to `none`. Any **unknown** future
#: vendor status normalizes to `none` (fail closed to Free entitlements, never to paid access)
#: and logs loudly."*
#:
#: The unknown case is the one worth writing down: Stripe adds statuses, and a status string
#: passed through unmapped would reach `limits_for`, miss `_STATUS_TO_EFFECTIVE_PLAN`, and — by
#: that function's own fallback — resolve to Free anyway. Correct by luck, through two layers of
#: default. Mapping it here makes the fail-closed direction a decision at the boundary where the
#: unknown value first appears, and logs it so a new Stripe status is noticed rather than absorbed.
_STATUS_MAP = {
    "trialing": "trialing",
    "active": "active",
    "past_due": "past_due",
    "unpaid": "unpaid",
    "canceled": "canceled",
    "incomplete": "none",
    "incomplete_expired": "none",
    "paused": "none",
}


def _normalize_status(vendor_status: str | None) -> str | None:
    """Stripe status → the normalized set, failing closed on anything unrecognised.

    `None` in means no subscription, which is a legitimate state (D4) and passes through. An
    **unknown** string means Stripe shipped a status this code predates: it becomes `"none"` —
    Free entitlements — and logs at warning level, because the alternative is a value that
    silently falls through every downstream mapping and lands on paid access by accident.
    """
    if vendor_status is None:
        return None
    normalized = _STATUS_MAP.get(vendor_status)
    if normalized is None:
        logger.warning(
            "unknown Stripe subscription status %r — failing closed to Free entitlements "
            "(BILLING §5). Add it to _STATUS_MAP once its behaviour is decided.",
            vendor_status,
        )
        return "none"
    return normalized


def _get(obj, key: str, default=None):
    """Read an optional field from a `StripeObject` **or** a plain dict.

    The SDK's `StripeObject` supports `obj["key"]` but deliberately raises `AttributeError` on
    `obj.get("key")` — it is a `dict` lookalike that is not a `dict`, and the error message says
    so. Webhook payloads arrive as `StripeObject`s while `FakeBillingProvider` and the fixture
    payloads use dicts, so normalization has to handle both without a type check at every field.

    `try/except KeyError` rather than `hasattr`/`isinstance`: nested objects can be either type at
    any depth, and a lookup that works on both is simpler than a branch that has to be right about
    which one it received.
    """
    try:
        value = obj[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return default if value is None else value


class StripeProvider:
    """Satisfies `BillingProvider` structurally — no subclassing, per the `AIProvider` precedent."""

    def __init__(self, secret_key: str | None = None, webhook_secret: str | None = None) -> None:
        import stripe

        self._secret_key = secret_key or os.environ.get(SECRET_KEY_ENV)
        if not self._secret_key:
            raise BillingAuthError(
                f"Stripe secret key not found. Set the {SECRET_KEY_ENV} environment variable "
                f"to a **restricted** key scoped to customers, checkout sessions, subscriptions "
                f"and billing portal sessions (BILLING §9)."
            )
        # Read here, validated in handle_webhook_event — see the module docstring.
        self._webhook_secret = webhook_secret or os.environ.get(WEBHOOK_SECRET_ENV)

        stripe.api_key = self._secret_key
        self._stripe = stripe

    # -- customers ----------------------------------------------------------------------

    def create_customer(self, *, account_id: str, email: str, name: str) -> str:
        """`account_id` goes in Stripe metadata so a customer can be traced back during
        reconciliation even if our row is lost — the one direction the DB cannot answer."""
        customer = self._stripe.Customer.create(
            email=email, name=name, metadata={"account_id": account_id},
        )
        return customer.id

    # -- purchase + management ----------------------------------------------------------

    def create_checkout_session(self, *, customer_id: str, plan: str, interval: str,
                                success_url: str, cancel_url: str) -> str:
        """Takes `(plan, interval)` — **never a price id** (D3/N2).

        Resolution happens here, against env-loaded config, so a vendor price id exists in
        exactly one place and can never arrive from a client as a self-service discount.
        """
        from mihomes.services.billing.prices import price_id_for

        session = self._stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id_for(plan, interval), "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return session.url

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        session = self._stripe.billing_portal.Session.create(
            customer=customer_id, return_url=return_url,
        )
        return session.url

    def cancel(self, *, subscription_id: str, at_period_end: bool = True) -> None:
        if at_period_end:
            self._stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
        else:
            self._stripe.Subscription.delete(subscription_id)

    # -- reconciliation -----------------------------------------------------------------

    def get_subscription(self, *, customer_id: str) -> SubscriptionState:
        """Normalized, never a raw vendor object — the drift-correction read (D15's sweep).

        A customer with no subscription is **not** an error: D4 says Free accounts have no Stripe
        subscription object at all, so the empty state is the expected answer for most accounts.
        """
        subs = self._stripe.Subscription.list(customer=customer_id, status="all", limit=1)
        if not subs.data:
            return SubscriptionState(
                provider_subscription_id=None, plan=None, status=None,
                current_period_end=None, cancel_at_period_end=False,
            )
        return self._to_state(subs.data[0])

    # -- webhooks -----------------------------------------------------------------------

    def handle_webhook_event(self, *, payload: bytes, signature: str) -> NormalizedEvent | None:
        """Verify over **raw bytes**, then normalize (N3).

        `construct_event` parses *after* verifying, which is the whole point: a body that was
        parsed and re-serialized first no longer matches its signature — or worse, matches after
        we have already acted on unverified input.
        """
        if not self._webhook_secret:
            raise BillingAuthError(
                f"Stripe webhook secret not found. Set the {WEBHOOK_SECRET_ENV} environment "
                f"variable — without it no webhook can be verified, and webhooks are the only "
                f"thing that changes entitlements (D1)."
            )
        try:
            event = self._stripe.Webhook.construct_event(
                payload, signature, self._webhook_secret,
            )
        except self._stripe.error.SignatureVerificationError as exc:
            raise WebhookVerificationError(str(exc)) from exc
        except ValueError as exc:
            # Malformed payload — Stripe raises this before verification can even run.
            raise WebhookVerificationError(f"malformed webhook payload: {exc}") from exc

        return self._normalize(event)

    # -- normalization ------------------------------------------------------------------

    def _normalize(self, event) -> NormalizedEvent | None:
        """Stripe event → `NormalizedEvent`; `None` for types we deliberately ignore.

        **`StripeObject` is not a dict**, and this is worth stating because the SDK makes it look
        like one: it supports `[]` but raises `AttributeError` on `.get()`, with a message
        pointing at `.to_dict()`. So every optional field is read through `_get`, never `.get`.
        Found by running the real SDK against a real signed payload rather than a mock — a
        stubbed verifier returns whatever shape the test author imagined, which here would have
        been a plain dict and would have hidden this until the first live webhook.
        """
        mapped = _EVENT_TYPE_MAP.get(event["type"])
        if mapped is None:
            return None

        obj = event["data"]["object"]
        customer_id = _get(obj, "customer")
        if not customer_id:
            # No customer to map to an account. Returning None rather than raising: the route
            # still acks, so Stripe stops retrying an event we can never resolve.
            return None

        subscription = None
        if event["type"].startswith("customer.subscription."):
            subscription = self._to_state(obj)

        return NormalizedEvent(
            type=mapped,
            provider_customer_id=customer_id,
            subscription=subscription,
            raw_event_id=event["id"],
            occurred_at=datetime.fromtimestamp(event["created"], tz=UTC),
        )

    def _to_state(self, sub) -> SubscriptionState:
        """One subscription object → `SubscriptionState`.

        The **price id → plan** direction lives in `prices.py` alongside its inverse, so the two
        maps cannot drift into disagreeing about what a customer bought.

        Every read goes through `_get` — see `_normalize` on why `.get()` is not available here.
        """
        from mihomes.services.billing.prices import plan_for_price_id

        price_id = None
        items = _get(_get(sub, "items") or {}, "data") or []
        if items:
            price_id = _get(_get(items[0], "price") or {}, "id")

        period_end = _get(sub, "current_period_end")
        return SubscriptionState(
            provider_subscription_id=_get(sub, "id"),
            plan=plan_for_price_id(price_id) if price_id else None,
            status=_normalize_status(_get(sub, "status")),
            current_period_end=(
                datetime.fromtimestamp(period_end, tz=UTC) if period_end else None
            ),
            cancel_at_period_end=bool(_get(sub, "cancel_at_period_end")),
        )
