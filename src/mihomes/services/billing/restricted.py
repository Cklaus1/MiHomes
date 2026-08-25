"""Restricted mode — what an over-limit account may still do (`PRICING` §4.3, D9, A20).

> Policy is **humane and non-destructive**: we never delete data for a billing lapse.

Three paths arrive at the same state, which is why this lives in one module rather than at each
of them: **past-due after grace**, **voluntary downgrade or cancellation**, and **trial expiry**.
`PRICING` §4.3 names a fourth — arriving over-limit via import — and closes it at the source
instead (D16, Step 17: the importer refuses rather than creating an account this table would have
to rescue).

## Frozen is computed, never stored

There is no `properties.frozen` column, and adding one would be the obvious mistake. The set of
frozen homes is a *function* of `(homes, max_homes, owner's choice)`, and all three change: a
customer upgrades, the owner swaps which home is active, a plan's limits are revised. A stored
flag is a cache of that function with no invalidation, so it would silently disagree with the
plan the day any of them moved — and the disagreement would look like "why can't I edit my own
house".

Computing it also makes the non-destructive guarantee structural rather than remembered: there is
no write to get wrong, and no migration that could drop a row.

## The default is oldest-created, and it is a default rather than a rule

§4.3: *"the owner **chooses** which home(s) stay active … If no choice is made by the time
Restricted starts, default = keep the **oldest-created** home active, freeze the rest (newest
first)."*

Oldest-created because it is the one most likely to be the household's real home — the second and
third are the ones added while exploring. The picker (§4.3's "in-app picker shown from day 0 of
Grace") is Phase 4 UI work; what ships here is the fallback it overrides, and `restriction_for`
takes `chosen_ids` so the picker has somewhere to put its answer without a retrofit.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

__all__ = [
    "AccountRestriction",
    "is_property_frozen",
    "restriction_for",
]


class AccountRestriction:
    """What this account may still do, resolved once per caller.

    A small object rather than loose functions because every consumer needs the same three facts
    together — is anything restricted, which homes are active, how many seats are over — and
    recomputing them per question would mean three queries per page render.
    """

    def __init__(self, *, restricted: bool, active_ids: frozenset, frozen_ids: frozenset,
                 max_homes: int, max_seats: int) -> None:
        self.restricted = restricted
        self.active_ids = active_ids
        self.frozen_ids = frozen_ids
        self.max_homes = max_homes
        self.max_seats = max_seats

    def may_edit(self, property_id) -> bool:
        """§4.3: frozen homes are *"read-only, never deleted: view and export yes;
        create/edit/complete/AI-advise no."*"""
        return property_id not in self.frozen_ids

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AccountRestriction restricted={self.restricted} "
            f"active={len(self.active_ids)} frozen={len(self.frozen_ids)}>"
        )


def restriction_for(
    session: Session, account, *, chosen_ids: frozenset | None = None
) -> AccountRestriction:
    """Resolve restricted state for `account`.

    `chosen_ids` is the owner's picker selection when one exists. Ignored where it would break
    the limit — a choice of three homes on a one-home plan is not a choice, and honouring it
    would make the picker a way around the cap rather than a way to express a preference within
    it.

    **`past_due` is not restricted** (D10). Grace keeps full access while Stripe retries, so an
    account in dunning resolves here as unrestricted — `limits_for` already maps that status to
    the account's own plan, and this function reads the same source rather than re-deciding.
    """
    from mihomes.entitlements.limits import limits_for
    from mihomes.models.property import Property

    limits = limits_for(
        getattr(account, "plan", "free"), getattr(account, "subscription_status", None)
    )
    max_homes = limits.get("max_homes", 0)
    max_seats = limits.get("max_seats", 0)

    # Oldest first — §4.3's default, and the ordering the picker overrides rather than replaces.
    property_ids = list(
        session.execute(
            select(Property.id)
            .where(Property.account_id == account.id)
            .order_by(Property.created_at.asc(), Property.id.asc())
        ).scalars()
    )

    if len(property_ids) <= max_homes:
        return AccountRestriction(
            restricted=False,
            active_ids=frozenset(property_ids),
            frozen_ids=frozenset(),
            max_homes=max_homes,
            max_seats=max_seats,
        )

    active = _resolve_active(property_ids, chosen_ids, max_homes)
    frozen = frozenset(property_ids) - active

    logger.info(
        "account %s is restricted: %d home(s) active, %d frozen (max_homes=%d)",
        account.id, len(active), len(frozen), max_homes,
    )
    return AccountRestriction(
        restricted=True,
        active_ids=active,
        frozen_ids=frozen,
        max_homes=max_homes,
        max_seats=max_seats,
    )


def _resolve_active(property_ids: list, chosen_ids: frozenset | None, max_homes: int) -> frozenset:
    """Which homes stay active: the owner's choice where valid, oldest-created otherwise.

    A partial choice is honoured and **topped up** from the oldest remaining, rather than
    discarded. An owner who picks one home on a two-home plan has expressed a real preference
    about that home; throwing it away because they did not fill every slot would be the system
    being pedantic at the exact moment the customer is already unhappy.
    """
    if not chosen_ids:
        return frozenset(property_ids[:max_homes])

    valid = [pid for pid in property_ids if pid in chosen_ids][:max_homes]
    if len(valid) < max_homes:
        valid += [pid for pid in property_ids if pid not in valid][: max_homes - len(valid)]
    return frozenset(valid)


def is_property_frozen(session: Session, account, property_id) -> bool:
    """Convenience for a single check — the shape most call sites want.

    Deliberately re-resolves rather than caching: a stale answer here is a home the customer can
    edit when they should not, or cannot when they should, and both are worse than a query.
    """
    return property_id in restriction_for(session, account).frozen_ids
