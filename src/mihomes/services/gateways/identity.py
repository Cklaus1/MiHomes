"""Sender identity → account. **THE tenancy boundary** (SPEC-006 §5.1, D11, Step 2).

A gateway without tenancy does not fail closed — it fails into the *wrong account*. A row
appears in a stranger's estate and a cheerful confirmation goes back to the person who caused
it, and nobody is watching a screen when it happens. This module is the one place that decides
whose estate a message belongs to, and D11 puts it at the transport edge on purpose: resolve
once at ingress, never inside `dispatch_items`' fourteen category branches, because scoping in
each means fourteen chances to forget and the one that forgets is a leak nobody sees (N3).

## The name collision with `telegram_link_service.resolve_sender` is deliberate, not an oversight

SPEC-003 §6 Step 16 shipped a function of the same name. **They are not the same function and
neither can be expressed as the other**, which is why §3's file manifest puts this one in a new
module rather than extending that one:

|                | `telegram_link_service.resolve_sender`   | this module                        |
|----------------|------------------------------------------|------------------------------------|
| account        | an **input** parameter                   | the **output** — what is discovered |
| unlinked       | returns `None` → `UNLINKED_ROLE="staff"` | raises `UnlinkedSender` (D12/N2)   |
| returns        | `Membership | None`                      | `ResolvedSender`                   |

The data flows in opposite directions. SPEC-003's takes `account_id` as an argument and asks
"what may this sender do *within* this account"; A5's whole claim is that the account is what
gets discovered. That is not a refactor.

**Their unlinked behaviours also genuinely conflict, and both are load-bearing.** SPEC-003 D16
treats an unlinked sender as staff-level rather than denied, and says why: deny-by-default on
day one "would silence the bot for the entire Belle group — including the founder", since no
links existed yet. SPEC-006 D12 refuses outright. Both are right *for their own scope*:

- D16 answers **"which answers may an unlinked sender receive"** in a single-tenant deployment
  where the account is already known from config. Staff-level with an empty scope is the most
  restrictive combination available, and it fails closed on that question.
- D12 answers **"which account does this sender belong to"**, which has no safe default at all.
  A configured default is correct for one tenant and a cross-account *write* for many.

So SPEC-006 does not supersede D16 and this module does not touch `telegram_link_service.py`,
which is live on the Telegram path. Recorded as harness deviation **D6**, because a reader
meeting two functions of one name deserves to find the reasoning rather than reconstruct it.

## The one legitimately unscoped read

`resolve_sender` runs **before** any tenant context exists — establishing it is the entire
point — so the lookup cannot be scoped by the thing it is computing. §5.1 carves this out
explicitly, and it is the same carve-out `telegram_link_service.resolve_sender` and
`auth/sessions.py` already rely on: a **Core `select`** against `__table__`, which does not go
through the ORM's `do_orm_execute` tenancy listener and therefore does not demand the context
it is trying to establish. Every caller must then open a scoped session with the returned
`account_id` and do nothing else first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.membership import Membership
from mihomes.models.telegram_link import TelegramLink

__all__ = [
    "AmbiguousSender",
    "ResolvedSender",
    "UnlinkedSender",
    "resolve_sender",
]

_MEMBERSHIPS = Membership.__table__
_LINKS = TelegramLink.__table__


@dataclass(frozen=True)
class ResolvedSender:
    """Which account a sender belongs to, and as whom.

    `role` is `memberships.role` — the capability matrix's `owner`/`admin`/`staff` vocabulary,
    **never** `StaffRole` (D3/N7). The two must not be crossed: `StaffRole` is a *job* enum with
    its own `OWNER`, and a `StaffRole.OWNER` housekeeping record is not an account owner.

    Frozen because a resolved identity is a fact about a message that has already arrived.
    Anything that wants a different account must resolve again, visibly.
    """

    account_id: uuid.UUID
    membership_id: uuid.UUID
    role: str


class UnlinkedSender(Exception):
    """No link exists for this sender.

    **Not an error condition** — it is the expected first contact, and the caller replies with a
    linking prompt (D12). Raised rather than returned as `None` precisely because a `None` is
    easy to coalesce into a default, and the default is the failure this phase exists to
    prevent: it is silent, and the sender sees a normal-looking confirmation while the row lands
    in someone else's estate.
    """


class AmbiguousSender(Exception):
    """The sender is linked in more than one account and the message names none of them.

    Legitimate under D5 — the same person may be a member of two estates and reach the bot in
    both. In a *group* the chat identifies the account; in a **DM** nothing does, so this is
    refused with a disambiguation prompt rather than guessed (A7). Guessing here would be the
    D12 failure wearing a different hat: a plausible answer, silently wrong half the time.
    """

    def __init__(self, account_ids: list[uuid.UUID]):
        self.account_ids = account_ids
        super().__init__(
            f"sender is linked in {len(account_ids)} accounts and the message identifies none"
        )


def _links_for(session: Session, *, gateway: str, sender_id: str) -> list:
    """Every active link for this sender, across all accounts. **Deliberately unscoped.**

    A Core `select` for the reason the module docstring gives: this runs before tenant context
    exists. A revoked membership is excluded here and its link row also `CASCADE`s on deletion —
    two mechanisms, because the cascade only covers deletion while revocation is a status
    change, and a revoked member must lose the bot exactly as they lose the web app.
    """
    if gateway != "telegram":
        # WhatsApp links arrive with the Cloud API at Step 7. Refusing loudly beats resolving
        # against the Telegram table with a WhatsApp sender id: the namespaces are unrelated,
        # so a collision would bind the wrong human (the same hazard the per-gateway token
        # check in `linking.py` guards).
        raise UnlinkedSender(f"no link store for gateway {gateway!r}")

    try:
        telegram_user_id = int(sender_id)
    except (TypeError, ValueError):
        # A non-numeric Telegram sender id cannot match any row. Refusing is the same answer
        # the lookup would give, reached without a query.
        raise UnlinkedSender(f"malformed telegram sender id {sender_id!r}") from None

    return session.execute(
        select(
            _LINKS.c.account_id,
            _MEMBERSHIPS.c.id.label("membership_id"),
            _MEMBERSHIPS.c.role,
        )
        .select_from(
            _LINKS.join(_MEMBERSHIPS, _LINKS.c.membership_id == _MEMBERSHIPS.c.id)
        )
        .where(
            _LINKS.c.telegram_user_id == telegram_user_id,
            _MEMBERSHIPS.c.status == "active",
        )
    ).all()


def resolve_sender(
    session: Session,
    *,
    gateway: str,
    sender_id: str,
    chat_account_id: uuid.UUID | None = None,
) -> ResolvedSender:
    """Sender identity → account. Raises rather than defaulting (D12/N2).

    `chat_account_id` is how a *group* message disambiguates a sender who is legitimately
    linked in two accounts (D5): the chat belongs to exactly one account, so it names which.
    A DM carries no such signal and passes `None`, which is why a multi-account sender in a DM
    raises `AmbiguousSender` instead of being guessed (A7).

    Raises:
        UnlinkedSender: no active link — the expected first contact. Reply with a link prompt.
        AmbiguousSender: linked in several accounts and the message identifies none of them.
    """
    rows = _links_for(session, gateway=gateway, sender_id=sender_id)

    if not rows:
        raise UnlinkedSender(
            f"sender {sender_id!r} has no active {gateway} link in any account"
        )

    if chat_account_id is not None:
        for row in rows:
            if row.account_id == chat_account_id:
                return ResolvedSender(
                    account_id=row.account_id,
                    membership_id=row.membership_id,
                    role=row.role,
                )
        # The sender is linked somewhere, but not in the account this chat belongs to. That is
        # an unlinked sender *for this conversation*, and answering from one of their other
        # accounts would be precisely the cross-account write D12 forbids.
        raise UnlinkedSender(
            f"sender {sender_id!r} has no active {gateway} link in this chat's account"
        )

    if len(rows) > 1:
        raise AmbiguousSender([row.account_id for row in rows])

    row = rows[0]
    return ResolvedSender(
        account_id=row.account_id,
        membership_id=row.membership_id,
        role=row.role,
    )
