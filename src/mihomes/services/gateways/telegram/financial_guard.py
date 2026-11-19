"""D15/D17 — financial answers, and where they may be sent.

**D15: the bot is scoped by sender.** Founder decision: staff must not get financial answers from
the bot.

**D17 is the half that scoping alone cannot fix.** *"Scoping by asker alone still leaks: the bot
replies into a shared group, so an owner's answer about monthly spend is read by every staff
member in the chat."* The asker is authorised; the **audience** is not. So a financial answer is
never posted into a group containing staff — the bot offers a DM instead.

That distinction is why this is a separate module from `telegram_link_service`: one answers *may
this person ask?*, the other answers *may this room hear it?*, and conflating them is how the
second gets forgotten.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.membership import Membership
from mihomes.models.telegram_link import TelegramLink

__all__ = [
    "DM_OFFER",
    "STAFF_REFUSAL",
    "group_contains_staff",
    "is_financial_question",
    "screen_financial_answer",
]

#: Keyword screen for "is this a money question?".
#:
#: **Deliberately generous, and the asymmetry is the point.** A false positive offers a DM for a
#: question that did not need one — mildly annoying. A false negative posts the household's
#: spending into a group with the housekeeper in it. There is no symmetric tuning to be done here.
_FINANCIAL_MARKERS = (
    "spend", "spent", "cost", "costs", "budget", "invoice", "price", "paid", "pay",
    "expense", "expenses", "bill", "bills", "money", "dollar", "financial", "finance",
    "cheap", "expensive", "total", "salary", "wage", "payroll", "insurance premium",
)

STAFF_REFUSAL = (
    "I can't share financial information. Ask an account owner or admin if you need it."
)

DM_OFFER = (
    "That's a financial question, and this group includes people who can't see finances. "
    "Message me directly and I'll answer there."
)


def is_financial_question(text: str) -> bool:
    """Whether this message is asking about money.

    Substring matching on a generous marker list — see `_FINANCIAL_MARKERS` for why the bias is
    towards over-triggering. This is a *routing* decision, not an authorization one: the
    authorization is `finance.view` in the matrix, which the AI path already enforces (G10).
    """
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _FINANCIAL_MARKERS)


def group_contains_staff(
    session: Session, account_id: uuid.UUID, member_telegram_ids: list[int]
) -> bool:
    """Whether any linked member of this chat is staff — **or any member is unlinked**.

    The unlinked case is the one worth stating: D16 treats an unlinked sender as staff-level, so
    an unlinked *participant* must count as staff here too. Otherwise the guard would be strictest
    for accounts that have done the work of linking everyone, and weakest for the ones that have
    not — exactly backwards.

    An empty list is treated as "unknown", which counts as containing staff. A group whose
    membership the bot cannot enumerate is not a group it should read finances into.
    """
    if not member_telegram_ids:
        return True

    rows = session.execute(
        select(TelegramLink.__table__.c.telegram_user_id, Membership.__table__.c.role)
        .select_from(
            TelegramLink.__table__.join(
                Membership.__table__,
                TelegramLink.__table__.c.membership_id == Membership.__table__.c.id,
            )
        )
        .where(
            TelegramLink.__table__.c.account_id == account_id,
            TelegramLink.__table__.c.telegram_user_id.in_(member_telegram_ids),
            Membership.__table__.c.status == "active",
        )
    ).all()

    linked = {row.telegram_user_id: row.role for row in rows}

    for telegram_id in member_telegram_ids:
        role = linked.get(telegram_id)
        if role is None or role == "staff":
            # Unlinked → staff-level (D16). Staff → staff.
            return True
    return False


def screen_financial_answer(
    session: Session,
    account_id: uuid.UUID,
    asker_role: str,
    question: str,
    is_group: bool,
    member_telegram_ids: list[int] | None = None,
) -> str | None:
    """`None` to answer in place, or the text to send instead.

    Two gates in order, because they fail for different reasons and the user should be told the
    right one:

    1. **The asker** — staff never get a financial answer at all (D15), anywhere, DM included.
       Returning `STAFF_REFUSAL` rather than silence matters: a bot that ignores the question
       reads as broken, and the person will ask a colleague instead.
    2. **The audience** — an authorised asker in a group containing staff gets a DM offer (D17),
       not the number. The answer is not refused, only redirected; refusing it would punish the
       owner for the room they happen to be standing in.
    """
    if not is_financial_question(question):
        return None

    if asker_role not in ("owner", "admin"):
        return STAFF_REFUSAL

    if is_group and group_contains_staff(session, account_id, member_telegram_ids or []):
        return DM_OFFER

    return None
