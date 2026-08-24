"""Billing provider abstraction — Protocol, exceptions, factory.

**Transcribed from `BILLING` §4.1, deliberately verbatim.** SPEC-004 §5.1: *"Reuse them verbatim
— do not redeclare, rename, or 'improve' them."* The declarations below are the contract every
later step in this phase is written against, so a field renamed here for readability is a
divergence that Steps 5, 7, 12 and 18 all inherit silently.

**The one rule this module exists to enforce (D2/N5):** the adapter is stateless and DB-free.
`NormalizedEvent` carries *provider* identifiers and no `account_id`, because a raw webhook only
knows the provider's ids — mapping `provider_customer_id -> account` is `BillingService`'s job.
The moment this file needs a `Session`, the seam is gone: the adapter stops being swappable, the
fake stops being sufficient, and every test in the phase needs a database to prove a parsing
concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = [
    "BillingAuthError",
    "BillingProvider",
    "BillingProviderError",
    "NormalizedEvent",
    "SubscriptionState",
    "WebhookVerificationError",
    "get_billing_provider",
]


class BillingProviderError(Exception):
    """Base for every error the adapter raises.

    One `except BillingProviderError` at the route boundary must catch everything — a sibling
    class would escape it and turn a handled failure into a 500 on the webhook path, which
    Stripe then retries.
    """


class BillingAuthError(BillingProviderError):
    """API key missing, invalid, or insufficiently scoped."""


class WebhookVerificationError(BillingProviderError):
    """Signature verification failed. **Never** treat as transient: N3."""


@dataclass(frozen=True)
class SubscriptionState:
    """Vendor-neutral snapshot of a customer's subscription."""

    provider_subscription_id: str | None
    plan: str | None            # "free" | "pro" | "estate"
    status: str | None          # normalized status (see BILLING §5 mapping)
    current_period_end: datetime | None
    cancel_at_period_end: bool


@dataclass(frozen=True)
class NormalizedEvent:
    """Vendor-neutral billing event consumed by `BillingService`/entitlements.

    Deliberately carries *provider* identifiers, not a MiHomes `account_id` — a raw webhook only
    knows the provider's customer/subscription ids. Mapping `provider_customer_id -> account` is
    `BillingService`'s job (via `account.stripe_customer_id`), never the provider adapter's: the
    adapter must stay stateless and DB-free.
    """

    type: str                   # "subscription.activated" | "subscription.updated" |
                                # "subscription.cancelled" | "subscription.trial_will_end" |
                                # "invoice.paid" | "invoice.payment_failed"
    provider_customer_id: str
    subscription: SubscriptionState | None
    raw_event_id: str           # provider event id, for idempotency
    occurred_at: datetime       # provider timestamp, for out-of-order handling (§6)


class BillingProvider(Protocol):
    def create_customer(self, *, account_id: str, email: str, name: str) -> str:
        """Create a billing customer (`account_id` stored as provider metadata for
        reconciliation); returns the provider customer id."""
        ...

    def create_checkout_session(self, *, customer_id: str, plan: str, interval: str,
                                success_url: str, cancel_url: str) -> str:
        """Start a subscription purchase for `(plan, interval)` — e.g. `("pro", "monthly")`.

        Returns a hosted checkout URL. The plan→price-id mapping is provider-internal config
        (§9); **a vendor price id must never appear in the interface or arrive from the client**
        (D3/N2) — a client-supplied price id is a self-service discount.
        """
        ...

    def get_subscription(self, *, customer_id: str) -> SubscriptionState:
        """Fetch current subscription state, normalized — used for reconciliation, never
        returns a raw vendor object."""
        ...

    def cancel(self, *, subscription_id: str, at_period_end: bool = True) -> None: ...

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        """Self-serve management; returns a hosted Customer Portal URL."""
        ...

    def handle_webhook_event(self, *, payload: bytes, signature: str) -> NormalizedEvent | None:
        """Verify the signature and normalize a provider event.

        Returns `None` for event types we deliberately ignore (still ack them with 2xx); raises
        `WebhookVerificationError` on a bad signature. Verification is over **raw bytes** — a
        parsed-then-reserialized body does not verify (N3).
        """
        ...


def get_billing_provider(provider_name: str = "stripe") -> BillingProvider:
    """Mirrors `services/ai/provider.py`'s shape exactly: string dispatch, lazy per-branch
    import, explicit `else: raise` naming the supported list.

    **Takes no key.** `BILLING` §9 requires credentials to come from the environment, never from
    a caller and never from `configurations.value` (N12, and SPEC-003 N11 before it). A key
    parameter is how the second one happens by accident — a caller reaches for the nearest
    config value and a Stripe secret lands in a database column.
    """
    if provider_name == "stripe":
        from mihomes.services.billing.stripe_provider import StripeProvider
        return StripeProvider()
    raise BillingProviderError(
        f"Unknown billing provider: {provider_name}. Supported: stripe"
    )
