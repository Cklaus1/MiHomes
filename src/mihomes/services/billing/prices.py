"""`(plan, interval) -> price id`, and its inverse — the **only** place Stripe price ids exist.

**D3/N2 — a price id never appears in an interface and never arrives from a client.** A
client-supplied price id is a self-service discount: the caller picks what they pay. So
`create_checkout_session` takes `(plan, interval)` and resolves here, against the environment.

**Both directions live in this module on purpose.** `BILLING` §4.2: *"it and the reverse map
(price id → plan, used by `_normalize`) are the **only** places Stripe price ids exist."* Split
across two modules they drift, and a drifted pair is silent in the worst possible way — a
customer buys Pro and the webhook records something else, so the gate and the invoice disagree
and only the customer notices.

**Env vars, never literals, and this half is not negotiable (O1, §1.3, C11).** A price id is
deployment identity: test-mode and live-mode ids differ, and a literal shipped to production is a
real charge at the wrong price. The *limits* (`max_homes`, `ai_calls_per_month`) are the opposite
— product definition, already committed in `entitlements/limits.py` and drift-gated there. The
harness records that split as C11 because the two look like the same question and are not.

Resolution follows `ai_config.py`'s precedent — env, then raise, with the missing variable named
in the message so a misconfiguration is self-diagnosing at the point of failure.
"""

from __future__ import annotations

import os

__all__ = [
    "PRICE_ENV_VARS",
    "plan_for_price_id",
    "price_id_for",
]

#: `(plan, interval)` → the env var holding that combination's Stripe price id.
#:
#: Four entries, not six: **Free has no Stripe object at all** (D4 — *"(no subscription row) →
#: Free, the default state of every account"*). Stripe objects are created at conversion only, so
#: a `STRIPE_PRICE_FREE_MONTHLY` would name something that must never exist.
PRICE_ENV_VARS: dict[tuple[str, str], str] = {
    ("pro", "monthly"): "STRIPE_PRICE_PRO_MONTHLY",
    ("pro", "annual"): "STRIPE_PRICE_PRO_ANNUAL",
    ("estate", "monthly"): "STRIPE_PRICE_ESTATE_MONTHLY",
    ("estate", "annual"): "STRIPE_PRICE_ESTATE_ANNUAL",
}


class PriceConfigurationError(Exception):
    """A plan/interval combination has no configured price id.

    Deliberately **not** a `BillingProviderError`: this is a deployment misconfiguration, not a
    vendor failure, and the route boundary's `except BillingProviderError` should not quietly
    swallow it into a generic billing error. It means someone must set an env var.
    """


def price_id_for(plan: str, interval: str) -> str:
    """Resolve `(plan, interval)` to a Stripe price id from the environment.

    Raises `PriceConfigurationError` naming the missing variable — the whole reason the map is
    keyed by var name rather than holding ids directly.
    """
    key = (plan, interval)
    env_var = PRICE_ENV_VARS.get(key)
    if env_var is None:
        supported = ", ".join(f"{p}/{i}" for p, i in sorted(PRICE_ENV_VARS))
        raise PriceConfigurationError(
            f"No price configured for plan={plan!r} interval={interval!r}. Supported: {supported}"
        )

    price_id = os.environ.get(env_var)
    if not price_id:
        raise PriceConfigurationError(
            f"{env_var} is not set — required to sell {plan}/{interval}. Create the Stripe "
            f"Product and Price, then set this variable (SPEC-004 O1)."
        )
    return price_id


def plan_for_price_id(price_id: str) -> str | None:
    """The inverse — which plan a Stripe price id represents.

    `None` when the id matches nothing configured. That is the honest answer and it must **not**
    be defaulted to `"free"`: a price id we do not recognise means a customer bought something
    this deployment cannot name, and recording it as Free would silently downgrade a paying
    account. The caller decides what to do with the uncertainty.

    Built per call rather than cached at import, because the env is read at *use* time — a test
    that sets `STRIPE_PRICE_*` with `monkeypatch` must be visible here, and a module-level cache
    would freeze whatever the environment held when the first import happened.
    """
    if not price_id:
        return None
    for (plan, _interval), env_var in PRICE_ENV_VARS.items():
        if os.environ.get(env_var) == price_id:
            return plan
    return None
