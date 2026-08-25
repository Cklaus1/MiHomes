"""The AI usage meter — record a call, read the counter, resolve the billing period.

`PRICING` §5.1 bills **calls, not tokens**, so the counter is the billing unit and the event log's
token columns are telemetry. §10 records the consequence honestly: a user sending very long
contexts costs materially more per call than the pricing model assumes, and nothing acts on that
until metered billing (Phase 4+).

## Why the increment shares the event's transaction

`PRICING` §3.2 rule 5: checks fire *"server-side and transactionally, so races cannot exceed a
limit"*. A counter updated in a second transaction is a counter two concurrent calls can both read
stale — both see 199 of 200, both proceed, and the cap is exceeded by exactly as many requests as
arrive at once. Writing the event and the increment together makes the row lock do the
arbitration.

## Why the period is a date range and not "this month"

§5.1 resets on the **billing anniversary**: a Pro customer who subscribed on the 20th resets on
the 20th. Free accounts have no anniversary and use the calendar month. Both are calendar
questions with no time of day, which is why the columns are `Date`.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mihomes.models.ai_usage import AIUsageEvent, AIUsageRollup

logger = logging.getLogger(__name__)

__all__ = ["billing_period", "check_and_reserve", "current_usage", "record_usage"]


def billing_period(account, today: date | None = None) -> tuple[date, date]:
    """The current billing period as `(start, end)`, both inclusive of their own day.

    Anchored to `current_period_end`'s **day of month** when a subscription exists, so the meter
    resets when Stripe bills rather than when the calendar does — otherwise a customer who
    subscribed on the 20th would get a partial first month and a free stretch at every boundary.

    Falls back to the calendar month for Free accounts (D4: no subscription object) and for any
    account whose period end is not yet known.

    **Month arithmetic without `dateutil`**, which is not a dependency here: clamp the anchor day
    to the target month's length, so a 31st anchor lands on the 30th in April and the 28th in
    February rather than raising.
    """
    today = today or datetime.now(UTC).date()

    period_end = getattr(account, "current_period_end", None)
    anchor_day = period_end.day if period_end is not None else 1

    start = _clamp(today.year, today.month, anchor_day)
    if start > today:
        # The anchor has not come round yet this month, so the period began last month.
        year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        start = _clamp(year, month, anchor_day)

    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    next_start = _clamp(year, month, anchor_day)
    return start, _day_before(next_start)


def _clamp(year: int, month: int, day: int) -> date:
    """`date(year, month, day)` with the day clamped to the month's length."""
    import calendar

    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _day_before(value: date) -> date:
    from datetime import timedelta

    return value - timedelta(days=1)


def record_usage(
    session: Session,
    account,
    *,
    entry_point: str,
    provider: str,
    method: str,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> AIUsageRollup:
    """Insert the event and increment the rollup in **one** transaction (rule 5).

    Returns the rollup so a caller that needs the new count — the overage nudges, Step 11 — does
    not have to re-read it.

    The rollup is fetched `with_for_update()`: two concurrent calls at the cap must not both
    read the same `calls_used` and both decide there is room. The row lock is what makes the
    increment serial, and it is held only for the duration of this transaction.
    """
    start, end = billing_period(account)
    rollup = _rollup_for(session, account, start, end)

    session.add(
        AIUsageEvent(
            account_id=account.id,
            occurred_at=datetime.now(UTC),
            entry_point=entry_point,
            provider=provider,
            method=method,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
    )
    rollup.calls_used += 1
    session.commit()
    return rollup


def current_usage(session: Session, account) -> int:
    """Calls used in the current billing period. **Reads the rollup, never the event log.**

    A `COUNT(*)` over `ai_usage_events` would be correct and would also grow linearly on the hot
    path of every AI call — the reason D18 materializes the counter in the first place.

    Returns 0 rather than creating a row: reading usage must not write, or a dashboard render
    would provision periods for accounts that never made a call.
    """
    start, _end = billing_period(account)
    used = session.execute(
        select(AIUsageRollup.calls_used).where(
            AIUsageRollup.account_id == account.id,
            AIUsageRollup.period_start == start,
        )
    ).scalar_one_or_none()
    return used or 0


def check_and_reserve(session: Session, account, *, entry_point: str):
    """**DEFERRED to Step 11** — the hard ceiling, the soft cap and the 80/100% nudges.

    Declared now because `MeteredProvider` is written against it and Step 11 fills in the body;
    an `Allowed` here means the meter counts without yet denying, which is deliberate: Step 10's
    criterion (A11) is that every path is *counted*, and Step 11's (A14) is that the count is
    *enforced*. Landing them together would make a failure of either look like a failure of both.
    """
    from mihomes.entitlements.service import Allowed

    return Allowed()


def _rollup_for(session: Session, account, start: date, end: date) -> AIUsageRollup:
    """Fetch this period's rollup for update, creating it on first use.

    Insert-first on the unique constraint, the same shape as the webhook ledger and for the same
    reason (N4): `SELECT`-then-`INSERT` races, and the first two AI calls of a billing period can
    easily be concurrent. The loser catches the violation and re-reads the winner's row.
    """
    stmt = (
        select(AIUsageRollup)
        .where(
            AIUsageRollup.account_id == account.id,
            AIUsageRollup.period_start == start,
        )
        .with_for_update()
    )
    rollup = session.execute(stmt).scalar_one_or_none()
    if rollup is not None:
        return rollup

    rollup = AIUsageRollup(
        account_id=account.id, period_start=start, period_end=end, calls_used=0,
    )
    session.add(rollup)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        rollup = session.execute(stmt).scalar_one()
    return rollup
