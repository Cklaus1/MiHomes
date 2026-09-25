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

from dataclasses import dataclass, field

from mihomes.entitlements.limits import PLAN_LIMITS, UNLIMITED, effective_plan
from mihomes.services.billing.cancel import has_live_subscription

PLAN_ORDER = ("free", "pro", "estate")

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


def feature_labels(plan: str) -> list[tuple[bool, str]]:
    """A plan's (included, label) rows — the same list its card shows."""
    return _features(PLAN_LIMITS[plan])


def plan_cards(account) -> list[PlanCard]:
    current = effective_plan(account.plan, account.subscription_status)
    rank = PLAN_ORDER.index(current) if current in PLAN_ORDER else 0
    paying = has_live_subscription(account)
    cancelling = paying and bool(getattr(account, "cancel_at_period_end", False))
    trialing = account.subscription_status == "trialing" and account.trial_ends_at is not None

    cards = []
    for i, key in enumerate(PLAN_ORDER):
        card = PlanCard(key=key, name=key.capitalize(), tagline=_TAGLINES[key],
                        features=_features(PLAN_LIMITS[key]), current=(key == current))
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
