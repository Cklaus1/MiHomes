"""THE intra-account scope primitive — SPEC-003 §4.3 (A10, A11).

*"One implementation, four consumers"*: web queries, the AI advisor's 15 executors, the bot's Q&A
path, and the bot's classification path. §4.3's reason for insisting on one — *"written
separately they drift, and drift is a leak"* — is the reason this module is deliberately tiny and
has no siblings.

**Signature note (pre-flight C3's rule, applied here).** §4.3 writes
`scoped_property_ids(membership)`, but answering for owner/admin requires reading *every property
in the account*, which needs a session. The session is therefore a required leading positional.
The rule §4.3 actually encodes — **required, never optional, so a forgetting call site fails
loudly rather than silently receiving full access** — is preserved; the literal one-argument line
is not, because it cannot be implemented.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.membership import Membership, MembershipPropertyScope
from mihomes.models.property import Property

__all__ = ["scoped_property_ids"]

# Roles whose scope rows are ignored (`ONBOARDING:44`). Kept as a set rather than an `in
# ("owner", "admin")` literal at the comparison site so there is exactly one place to change if a
# fourth role is ever introduced — and so a new role defaults to the *staff* branch, which is the
# fail-closed direction.
_PRIVILEGED_ROLES = frozenset({"owner", "admin"})


def scoped_property_ids(session: Session, membership: Membership) -> frozenset[uuid.UUID]:
    """The set of properties this membership may see. **The authorization boundary.**

    - `owner`/`admin` → every property in the account, *their scope rows ignored*
      (`ONBOARDING:44`).
    - `staff` → exactly their `membership_property_scopes` rows.
    - staff with zero scope rows → `frozenset()` — zero properties, **never "all"** (D3).

    **The empty set is a real answer, not a missing filter.** The natural implementation — build
    a filter from the scope rows, and apply no filter when there are none — reads as "no
    restriction" and returns the whole account. That is D3's failure mode, and it looks exactly
    like the feature working, so callers must treat `frozenset()` as "show nothing" rather than
    as "unfiltered".

    A revoked membership yields `frozenset()` regardless of role. Scope rows survive revocation
    (they cascade on *delete*, not on a status change), so a primitive keyed only on
    `membership_id` would keep honouring them — and for an owner the privileged branch would
    *upgrade* a revoked member from "their scope rows" to "everything".
    """
    if membership.status != "active":
        return frozenset()

    if membership.role in _PRIVILEGED_ROLES:
        rows = session.execute(
            # `Property` is TenantOwned, so the G8 filter already constrains this to the current
            # account. The explicit predicate is belt-and-braces: this is the one query here not
            # naturally keyed on a single row, so it is where a missing tenant context would turn
            # into a cross-account read rather than an error.
            select(Property.id).where(Property.account_id == membership.account_id)
        ).scalars()
        return frozenset(rows)

    rows = session.execute(
        select(MembershipPropertyScope.property_id).where(
            MembershipPropertyScope.membership_id == membership.id,
            MembershipPropertyScope.account_id == membership.account_id,
        )
    ).scalars()
    return frozenset(rows)
