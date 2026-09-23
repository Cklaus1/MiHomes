"""The plan comparison on /billing — three cards, each saying what it includes and what to press.

**Built from `PLAN_LIMITS`, never typed into the template.** Every number there except Free's
1 home / 3 seats is `PLACEHOLDER` (`PRICING` §3.1) and will change; a second copy in HTML would
go on promising the old numbers after the gates started enforcing new ones.

**What each card offers depends on how the account pays, not only on its plan:**

- **Paying through Stripe** (`stripe_subscription_id` set): every change — up, down, or cancel to
  Free — goes through the Stripe Customer Portal. Never a checkout form: `/billing/checkout` would
  open a *second* subscription on the same customer and bill them twice.
- **No subscription** (Free, a no-card trial, or a hand-seeded plan): checkout for the plans above
  the current one. Nothing to press for a lower plan: there is no subscription to cancel, and
  `"free"` is deliberately not sellable (D4) — plan changes arrive through the verified webhook
  path, not a form that writes `plan`. A trial says when it returns to Free by itself.

Keyed on `stripe_subscription_id`, **not** `stripe_customer_id`: `start_checkout` commits the
customer id before the user reaches Stripe, so an abandoned checkout used to leave an account with
a customer, no subscription, and only a "Manage billing" button — no way to try again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mihomes.entitlements.limits import PLAN_LIMITS, UNLIMITED, effective_plan

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
    #: "checkout" | "portal" | None — what the card's button does.
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


def plan_cards(account) -> list[PlanCard]:
    current = effective_plan(account.plan, account.subscription_status)
    rank = PLAN_ORDER.index(current) if current in PLAN_ORDER else 0
    paying = bool(getattr(account, "stripe_subscription_id", None))
    trialing = account.subscription_status == "trialing" and account.trial_ends_at is not None

    cards = []
    for i, key in enumerate(PLAN_ORDER):
        card = PlanCard(key=key, name=key.capitalize(), tagline=_TAGLINES[key],
                        features=_features(PLAN_LIMITS[key]), current=(key == current))
        if card.current:
            pass
        elif paying:
            card.action = "portal"
            if key == "free":
                card.action_label = "Cancel subscription"
            else:
                card.action_label = f"{'Upgrade' if i > rank else 'Switch'} to {card.name}"
        elif i > rank:
            card.action = "checkout"
            card.action_label = f"Upgrade to {card.name}"
        elif key == "free" and trialing:
            card.note = (
                f"Your trial returns to Free on {account.trial_ends_at.strftime('%d %b %Y')}"
                " unless you subscribe."
            )
        cards.append(card)
    return cards
