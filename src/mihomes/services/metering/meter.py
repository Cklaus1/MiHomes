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


def hard_ceiling(account) -> int:
    """`ai_calls_per_month × (1 + ai_overage_buffer_pct/100)` — `PRICING` §5.3.

    **Not the limit.** The limit is the soft cap: past it, AI keeps working through the buffer
    with an upgrade banner, because *"a hard wall at exactly the limit punishes the most engaged
    users — our best upgrade candidates — and creates a bad moment."* The ceiling exists purely to
    bound worst-case Claude spend.

    Free's buffer is **0%**, so its soft cap and ceiling coincide at 200: there is no revenue to
    offset the cost of letting it run over. That is a deliberate asymmetry, not an oversight.
    """
    from mihomes.entitlements.limits import limits_for

    limits = limits_for(
        getattr(account, "plan", "free"), getattr(account, "subscription_status", None)
    )
    cap = limits.get("ai_calls_per_month", 0)
    buffer_pct = limits.get("ai_overage_buffer_pct", 0)
    return int(cap * (1 + buffer_pct / 100))


def check_and_reserve(session: Session, account, *, entry_point: str):
    """Called **before** dispatch. `Denied` once the account is at its hard ceiling (A14).

    §5.3: *"the AI dispatch path compares against the hard ceiling **before** invoking the
    provider; attempted calls past the ceiling are rejected, **not recorded**."* Both halves
    matter. Checking after would bill the customer for the call that was refused, and recording
    the rejection would inflate the counter past the ceiling forever, so the reset date would
    arrive with the account still over.

    **The name says `reserve` and it deliberately does not.** Reserving would mean incrementing
    here and decrementing on failure — two writes and a rollback path around every AI call, to
    close a window measured in milliseconds. The count is incremented by `record_usage` after the
    provider returns, so the only overshoot is the calls in flight when the ceiling is crossed,
    bounded by concurrency rather than by time. The signature is §5.4's; the behaviour is what the
    ordering rule in §5.3 actually requires.

    Fires the 80% and 100% nudges as a side effect, each **once per period**.

    **Two sessions per AI call, measured and accepted.** `MeteredProvider._check` opens one here
    and `_record` opens another after the provider returns, so the count read here can be stale by
    the time the increment lands. That is the same window the no-reserve decision above already
    accepts, and closing it would mean the reservation this function deliberately is not. Recorded
    so the next reader does not "fix" it into one.
    """
    from mihomes.entitlements.limits import limits_for
    from mihomes.entitlements.service import Allowed, Denied

    limits = limits_for(
        getattr(account, "plan", "free"), getattr(account, "subscription_status", None)
    )
    soft_cap = limits.get("ai_calls_per_month", 0)
    ceiling = hard_ceiling(account)
    used = current_usage(session, account)

    _maybe_nudge(session, account, used=used, soft_cap=soft_cap)

    if used >= ceiling:
        _start, resets_at = billing_period(account)
        from mihomes.entitlements.limits import UPGRADE_PATH

        return Denied(
            reason=(
                f"AI paused until {resets_at.isoformat()} — {used} of {soft_cap} calls used "
                f"this period. Upgrade to continue."
            ),
            upgrade_target=UPGRADE_PATH.get(getattr(account, "plan", "free")),
            limit=soft_cap,
        )
    return Allowed(limit=soft_cap)


def _maybe_nudge(session: Session, account, *, used: int, soft_cap: int) -> None:
    """Set the 80% / 100% markers, each once per period (§5.3, §5.1's *"once per cycle"*).

    **Columns rather than a recomputation**, and that is the whole design: "have we told them
    yet" is a fact about what was *sent*, and deriving it from `used >= threshold` would re-send
    on every single call past the threshold — turning a helpful nudge into a notification storm
    precisely for the heaviest users, who are the best upgrade candidates.

    This marks; it does not send. Step 15 owns the email and reads these columns, so a nudge
    cannot be marked-but-unsent by a failure in the mail path.
    """
    if soft_cap <= 0:
        return

    start, _end = billing_period(account)
    rollup = session.execute(
        select(AIUsageRollup).where(
            AIUsageRollup.account_id == account.id,
            AIUsageRollup.period_start == start,
        )
    ).scalar_one_or_none()
    if rollup is None:
        return

    now = datetime.now(UTC)
    changed = False
    if used >= soft_cap and rollup.warned_100_at is None:
        rollup.warned_100_at = now
        changed = True
    # Not `elif`: an account crossing both thresholds in one jump — a burst, or a plan downgrade
    # that lowers the cap under existing usage — must still record that 80% was passed, or the
    # 80% nudge would fire later in a *subsequent* period and read as a false alarm.
    if used >= soft_cap * 0.8 and rollup.warned_80_at is None:
        rollup.warned_80_at = now
        changed = True

    if changed:
        session.commit()


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
