"""What a chat-gateway AI answer may draw on — narrowed to the people who will read it.

**The defect this closes.** Gateway dispatch runs in a background job with no role bound, and
`ai/context._denied_categories` returns *nothing denied* when no role is bound. So a question asked
in a Telegram group was answered as an unrestricted estate manager — budgets, contracts, insurance,
staff records, every property — and posted into a room whose members include staff, or (since
group binding) people with no MiHomes login at all. `telegram/financial_guard.py` was written for
exactly this and had no callers.

**The rule, by room:**

* **Group chat** — the audience can't be enumerated (Telegram gives a bot no member list, and a bound
  group's members are mostly unlinked), so per `financial_guard.group_contains_staff`'s convention an
  unknown audience counts as staff. Money questions get `DM_OFFER` (owner/admin asker) or
  `STAFF_REFUSAL`; every other answer runs as **staff scoped to the chat's property only**.
* **Private chat** — the audience is the asker. Owner/admin: unrestricted. Staff: staff-level and
  their own scoped properties, money refused. Anyone the bot can't place (WhatsApp senders, which
  have no membership link yet): staff-level with no properties — fail closed.

The restriction is `authz_context("staff", scope)`, the same binding a signed-in staff member's
web request gets, so the AI's context assembly (`_denied_categories`) and its tools' row filter
(`query_scope`) both apply. It wraps the answer only — never the write path (SPEC-013 D6).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.authz.scope import authz_context, scoped_property_ids
from mihomes.models.membership import Membership
from mihomes.models.property import Property
from mihomes.models.telegram_link import TelegramLink
from mihomes.services.gateways.telegram.financial_guard import (
    DM_OFFER,
    STAFF_REFUSAL,
    is_financial_question,
)

_PRIVILEGED = frozenset({"owner", "admin"})


def _sender_memberships(session: Session, gateway: str, messages: list[dict]) -> list[Membership | None]:
    """One entry per distinct sender: their active membership in this account, or None."""
    senders = {str(m.get("sender") or "") for m in messages}
    out: list[Membership | None] = []
    for sender in senders:
        membership = None
        if gateway == "telegram" and sender.isdigit():
            membership = session.execute(
                select(Membership)
                .join(TelegramLink, TelegramLink.membership_id == Membership.id)
                .where(TelegramLink.telegram_user_id == int(sender), Membership.status == "active")
            ).scalars().first()
        out.append(membership)
    return out


def _property_scope(session: Session, property_slug: str | None) -> frozenset[uuid.UUID]:
    if not property_slug:
        return frozenset()
    pid = session.execute(select(Property.id).where(Property.slug == property_slug)).scalar()
    return frozenset({pid}) if pid else frozenset()


@contextmanager
def gateway_answer_scope(
    session: Session,
    text: str,
    property_slug: str | None,
    messages: list[dict],
    *,
    money_gate: bool,
    gateway: str,
) -> Iterator[str | None]:
    """Yield replacement text, or None with the answer's restriction bound. See module docstring."""
    is_group = any(m.get("isGroup") for m in messages)
    askers = _sender_memberships(session, gateway, messages)
    # The most restrictive sender in the batch decides: one staff (or unplaceable) sender means
    # the answer may be read by — or was asked by — someone without owner/admin rights.
    privileged = bool(askers) and all(a is not None and a.role in _PRIVILEGED for a in askers)

    if money_gate and is_financial_question(text):
        if not privileged:
            yield STAFF_REFUSAL
            return
        if is_group:
            yield DM_OFFER
            return

    if is_group:
        with authz_context("staff", _property_scope(session, property_slug)):
            yield None
        return

    if privileged:
        yield None
        return

    asker = askers[0] if len(askers) == 1 else None
    scope = scoped_property_ids(session, asker) if asker is not None else frozenset()
    with authz_context("staff", scope):
        yield None


def answer_scope_for(gateway: str):
    """The adapter hook for one gateway (`GatewayAdapter.answer_scope`)."""
    def hook(session, text, property_slug, messages, *, money_gate):
        return gateway_answer_scope(session, text, property_slug, messages,
                                    money_gate=money_gate, gateway=gateway)
    return hook
