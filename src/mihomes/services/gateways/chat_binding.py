"""Group binding — `/linkchat` and `/unlinkchat` (2026-10-23).

A Telegram group bound to an account routes every message in it to that account, whoever sent it:
linked senders resolve within that account, senders without a link are processed as untrusted
(M27 keeps money, PTO and resolution actions from them) and are never prompted in the group. This
is how staff without MiHomes logins report through a household group such as Belle's.

**Who may bind.** Only a sender who has linked themselves (`/link CODE` by DM) and resolves to
exactly one account as `owner` or `admin`. Binding decides where a whole group's messages land, so
it is an account-management action, not something any member may do.

**One group, one account.** A chat bound to another account is refused without naming it — the
sender may have nothing to do with that estate, and naming it would leak tenant names.

Every reply here is a sentence safe to send into the group.
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mihomes.models.telegram_chat_binding import TelegramChatBinding
from mihomes.services.gateways.identity import (
    AmbiguousSender,
    UnlinkedSender,
    resolve_sender,
)
from mihomes.tenancy.connection import bind_account_guc
from mihomes.tenancy.context import account_context

logger = logging.getLogger(__name__)

_BINDINGS = TelegramChatBinding.__table__
_MANAGER_ROLES = ("owner", "admin")
#: `/linkchat` or `/linkchat@SomeBot` — Telegram appends the bot name when a group has several bots.
_COMMAND = re.compile(r"^/(linkchat|unlinkchat)(?:@\w+)?\s*$", re.IGNORECASE)

LINKED = "This group is now linked to your estate. Messages here will be logged."
UNLINKED = "This group is no longer linked. Messages here won't be logged."
NOT_A_GROUP = "/linkchat only works in a group chat."
LINK_YOURSELF_FIRST = "Link yourself first: send me /link <code> in a direct message, then try again."
AMBIGUOUS = (
    "You're linked to more than one estate, so I can't tell which this group is for. "
    "Ask an owner or admin who belongs to just that estate to send /linkchat."
)
MANAGERS_ONLY = "Only an owner or admin can link or unlink this group."
ALREADY_LINKED = "This group is already linked to your estate."
LINKED_ELSEWHERE = "This group is already linked to another estate."
NOT_LINKED = "This group isn't linked."


def parse_chat_command(text: str | None) -> str | None:
    """'linkchat' / 'unlinkchat' if the message is exactly that command, else None."""
    match = _COMMAND.match((text or "").strip())
    return match.group(1).lower() if match else None


def bound_account_for_chat(session: Session, chat_id: str) -> uuid.UUID | None:
    """The account a chat is bound to, or None. **Runs before any account is bound.**

    Under the bootstrap policy keyed on this one chat id (`rls.GATEWAY_BOOTSTRAP_POLICIES`) — the
    same mechanism as the sender lookup, for the same reason: this read is how the account is found.
    """
    from mihomes.tenancy.connection import GATEWAY_TELEGRAM_CHAT_GUC, bind_gateway_guc

    try:
        chat = int(chat_id)
    except (TypeError, ValueError):
        return None
    bind_gateway_guc(session, GATEWAY_TELEGRAM_CHAT_GUC, str(chat))
    return session.execute(
        select(_BINDINGS.c.account_id).where(_BINDINGS.c.chat_id == chat)
    ).scalar_one_or_none()


def _manager(session: Session, sender_id: str, *, account_id: uuid.UUID | None):
    """The resolved sender if they may manage this group's binding, else the refusal text."""
    try:
        resolved = resolve_sender(
            session, gateway="telegram", sender_id=sender_id, chat_account_id=account_id
        )
    except UnlinkedSender:
        return None, LINK_YOURSELF_FIRST if account_id is None else MANAGERS_ONLY
    except AmbiguousSender:
        return None, AMBIGUOUS
    if resolved.role not in _MANAGER_ROLES:
        return None, MANAGERS_ONLY
    return resolved, None


def link_chat(session: Session, *, chat_id: str, sender_id: str, is_group: bool) -> str:
    """Bind the chat to the sender's account. Returns the sentence to send into the chat."""
    if not is_group:
        return NOT_A_GROUP
    existing = bound_account_for_chat(session, chat_id)
    resolved, refusal = _manager(session, sender_id, account_id=None)
    if refusal:
        return refusal
    if existing is not None:
        return ALREADY_LINKED if existing == resolved.account_id else LINKED_ELSEWHERE

    # A tenant write: the ContextVar for the ORM stamp, the GUC for RLS's WITH CHECK.
    with account_context(resolved.account_id):
        bind_account_guc(session, resolved.account_id)
        session.add(TelegramChatBinding(
            chat_id=int(chat_id), bound_by_membership_id=resolved.membership_id,
        ))
        session.flush()
    logger.info("telegram chat %s bound to account %s", chat_id, resolved.account_id)
    return LINKED


def unlink_chat(session: Session, *, chat_id: str, sender_id: str) -> str:
    """Unbind the chat. Only a manager of the account it is bound to may."""
    existing = bound_account_for_chat(session, chat_id)
    if existing is None:
        return NOT_LINKED
    _resolved, refusal = _manager(session, sender_id, account_id=existing)
    if refusal:
        return refusal

    with account_context(existing):
        bind_account_guc(session, existing)
        session.execute(
            delete(TelegramChatBinding).where(TelegramChatBinding.chat_id == int(chat_id))
        )
        session.flush()
    logger.info("telegram chat %s unbound from account %s", chat_id, existing)
    return UNLINKED
