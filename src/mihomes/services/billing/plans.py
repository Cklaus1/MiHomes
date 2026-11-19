"""The plan comparison on /billing — three cards, each saying what it includes and what to press.

**Built from `PLAN_LIMITS`, never typed into the template.** Every number there except Free's
1 home / 3 seats is `PLACEHOLDER` (`PRICING` §3.1) and will change; a second copy in HTML would
go on promising the old numbers after the gates started enforcing new ones.

**What each card offers depends on how the account pays, not only on its plan:**

- **Free card, from any paid plan:** "Downgrade to Free" → the confirm page (`/billing/cancel`).
  A paid subscription is cancelled at period end (with Undo while pending); a trial or hand-set
  plan drops to Free at once (`services/billing/cancel.py`). A write that can only *lower* the
  plan cannot be abused to gain access, which is why this one is allowed outside the webhook.
- **Paying through Stripe** (`stripe_subscription_id` set): moving between paid plans posts to
  `/billing/change`, which opens Stripe's confirm screen for that exact price. Never a checkout
  form: `/billing/checkout` would open a *second* subscription on the same customer and bill
  them twice.
- **No subscription** (Free, a no-card trial, or a hand-seeded plan): checkout for the plans above
  the current one; "Downgrade to Pro" for Estate, through the same confirm page, applied at once.

"Paying" is `has_live_subscription` — a subscription id *and* a status Stripe can still modify —
**not** `stripe_customer_id` (committed before the user reaches Stripe, so an abandoned checkout
left no way to try again) and not the subscription id alone (never cleared when a subscription
ends, so a canceled account read as paying and was offered a "Keep" that could not work).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from mihomes.entitlements.limits import PLAN_LIMITS, UNLIMITED, effective_plan
from mihomes.services.billing.cancel import has_live_subscription

logger = logging.getLogger(__name__)

PLAN_ORDER = ("free", "pro", "estate")
PAID_PLANS = ("pro", "estate")
INTERVALS = ("monthly", "annual")

#: `(plan, interval)` → `PriceInfo`, for the life of the process. Safe because a Stripe Price's
#: amount cannot be edited: a new amount is a new price id, which is a new env var value and a
#: restart. Failures are not cached, so a Stripe outage heals on the next page load.
_price_cache: dict = {}

_TAGLINES = {
    "free": "Everything one household needs, for one home.",
    "pro": "For several homes, or a home with staff.",
    "estate": "For estates run by a team across many properties.",
}

_SUPPORT = {"community": "Community support", "email": "Email support",
            "priority": "Priority support"}


@dataclass
class PlanCard:
    key: str
    name: str
    tagline: str
    features: list[tuple[bool, str]] = field(default_factory=list)
    current: bool = False
    #: "checkout" | "change" | "cancel" | "downgrade" | "resume" | None — the card's button.
    action: str | None = None
    action_label: str = ""
    #: Shown instead of a button, e.g. when a trial will return to Free by itself.
    note: str = ""
    #: "monthly"/"annual" → display dict from `_display_price`; empty when Stripe is unreachable.
    prices: dict = field(default_factory=dict)
    #: Features this plan adds over the one below it — the "Everything in X, plus:" list.
    added: list[str] = field(default_factory=list)
    plus_of: str = ""


def _count(n: int, one: str, many: str) -> str:
    if n >= UNLIMITED:
        return f"Unlimited {many} (fair use)"
    return f"{n:,} {one if n == 1 else many}"


def _features(limits: dict) -> list[tuple[bool, str]]:
    """(included, label) — a plan's §3.1 row in words a household would use."""
    return [
        (True, _count(limits["max_homes"], "home", "homes")),
        (True, _count(limits["max_seats"], "seat", "seats") + " for family and team"),
        (limits["staff_invites_allowed"], "Invite household staff"),
        (True, _count(limits["ai_calls_per_month"], "AI request", "AI requests") + " a month"),
        (limits["vendor_ratings"], "Vendor ratings"),
        (limits["work_order_scheduling"], "Work-order scheduling"),
        (limits["predictive_maintenance"], "Predictive maintenance"),
        (limits["weekly_ai_report"], "Weekly AI estate report"),
        (limits["audit_export"], "Audit log export"),
        (True, _SUPPORT.get(limits["support_tier"], "Support")),
    ]


def _added_features(key: str) -> tuple[str, list[str]]:
    """("Pro", [...]) — the labels `key` includes that the plan below it lacks or words
    differently ("5 homes" vs "1 home"). Free has nothing below it: every included row."""
    i = PLAN_ORDER.index(key)
    mine = [label for ok, label in _features(PLAN_LIMITS[key]) if ok]
    if i == 0:
        return "", mine
    below = PLAN_ORDER[i - 1]
    theirs = {label for ok, label in _features(PLAN_LIMITS[below]) if ok}
    return below.capitalize(), [label for label in mine if label not in theirs]


def money(minor: int, currency: str) -> str:
    symbol = {"usd": "$", "eur": "€", "gbp": "£"}.get(currency.lower(), "")
    whole, cents = divmod(minor, 100)
    text = f"{whole:,}" if cents == 0 else f"{minor / 100:,.2f}"
    return f"{symbol}{text}" if symbol else f"{text} {currency.upper()}"


def plan_prices(provider=None) -> dict:
    """`{(plan, interval): PriceInfo}` for every price Stripe answered; missing keys mean "unknown".

    Never raises: a plan page without prices is better than an error page, and far better than a
    price that differs from the one the customer is charged.
    """
    missing = [(p, i) for p in PAID_PLANS for i in INTERVALS if (p, i) not in _price_cache]
    if missing:
        try:
            if provider is None:
                from mihomes.services.billing.provider import get_billing_provider
                provider = get_billing_provider("stripe")
            for plan, interval in missing:
                _price_cache[(plan, interval)] = provider.get_price(plan=plan, interval=interval)
        except Exception:
            logger.warning("plan prices unavailable — showing the plan page without them", exc_info=True)
    return {k: _price_cache[k] for k in _price_cache}


def _display_prices(plan: str, prices: dict) -> dict:
    """Both intervals in the words the card shows. Yearly shows its per-month equivalent with the
    yearly total and the saving beneath — the saving is computed, never typed."""
    monthly, annual = prices.get((plan, "monthly")), prices.get((plan, "annual"))
    out = {}
    if monthly:
        out["monthly"] = {"amount": money(monthly.amount, monthly.currency), "per": "/ month",
                          "note": "Billed monthly"}
    if annual:
        note = f"{money(annual.amount, annual.currency)} billed yearly"
        if monthly and monthly.currency == annual.currency and monthly.amount * 12 > annual.amount:
            note += f" · save {money(monthly.amount * 12 - annual.amount, annual.currency)}"
        out["annual"] = {"amount": money(round(annual.amount / 12), annual.currency), "per": "/ month",
                         "note": note}
    return out


def yearly_saving(prices: dict) -> dict:
    """Page-level copy for the switch and the switch-to-yearly prompt: the best percentage across
    paid plans, plus per-plan amounts. Empty when any price is unknown."""
    out = {}
    pcts = []
    for plan in PAID_PLANS:
        m, a = prices.get((plan, "monthly")), prices.get((plan, "annual"))
        if not (m and a) or m.currency != a.currency or m.amount * 12 <= a.amount:
            continue
        pcts.append(round(100 * (m.amount * 12 - a.amount) / (m.amount * 12)))
        out[plan] = {"saving": money(m.amount * 12 - a.amount, a.currency),
                     "monthly": money(m.amount, m.currency), "annual": money(a.amount, a.currency)}
    if pcts:
        out["pct"] = max(pcts)
    return out


def feature_labels(plan: str) -> list[tuple[bool, str]]:
    """A plan's (included, label) rows — the same list its card shows."""
    return _features(PLAN_LIMITS[plan])


def plan_cards(account, prices: dict | None = None) -> list[PlanCard]:
    prices = prices or {}
    current = effective_plan(account.plan, account.subscription_status)
    rank = PLAN_ORDER.index(current) if current in PLAN_ORDER else 0
    paying = has_live_subscription(account)
    cancelling = paying and bool(getattr(account, "cancel_at_period_end", False))
    trialing = account.subscription_status == "trialing" and account.trial_ends_at is not None

    cards = []
    for i, key in enumerate(PLAN_ORDER):
        card = PlanCard(key=key, name=key.capitalize(), tagline=_TAGLINES[key],
                        features=_features(PLAN_LIMITS[key]), current=(key == current),
                        prices=_display_prices(key, prices))
        card.plus_of, card.added = _added_features(key)
        if card.current:
            if cancelling:
                end = account.current_period_end
                card.note = (f"Ends {end.strftime('%d %b %Y')}, then Free." if end
                             else "Ends at the end of this billing period, then Free.")
                card.action, card.action_label = "resume", f"Keep {card.name}"
        elif key == "free":
            if cancelling:
                card.note = "The account moves here when the current period ends."
            else:
                card.action, card.action_label = "cancel", "Downgrade to Free"
                if trialing:
                    card.note = (
                        f"Your trial returns to Free on "
                        f"{account.trial_ends_at.strftime('%d %b %Y')} anyway."
                    )
        elif paying:
            card.action = "change"
            card.action_label = f"{'Upgrade' if i > rank else 'Switch'} to {card.name}"
        elif i > rank:
            card.action = "checkout"
            card.action_label = f"Upgrade to {card.name}"
        else:
            # A lower paid plan, not paying through Stripe (Estate → Pro): the same confirm page
            # as Downgrade to Free, applied at once.
            card.action = "downgrade"
            card.action_label = f"Downgrade to {card.name}"
        cards.append(card)
    return cards
