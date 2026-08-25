"""AI usage metering — an event log and a materialized monthly counter (SPEC-004 §4.2).

**Two tables, and the second one is the whole of D18.** The counter `usage()` returns is
materialized, never derived from `ai_conversations`, for two measured reasons:

- `archive.py:191-199` **DELETEs** `ai_conversations` rows when archiving (F10). A derived count
  would silently reset a customer's usage the moment they tidied up — a billing defect that looks
  like generosity until someone notices the bill.
- Only **5 of ~12** AI paths write an `ai_conversations` row at all (F9). Gateway reviews,
  assessors, resume ranking and weather tasks log nothing, so even before archiving the table was
  never a complete request log.

`ai_conversations.tokens_used` is dead in every row — zero assignments anywhere in `src/` — so
there is **no history to backfill**. The meter starts from zero by necessity rather than choice.

## Why an event log *and* a rollup

The rollup alone would bill correctly and answer nothing else: at audit time the question is
*"which entry points are metered"*, and a counter cannot say. The event log carries `entry_point`
for exactly that — A11 asserts every dispatch path produces one — and `tokens_in`/`tokens_out` so
inference cost becomes measurable later (§10 records that nothing acts on it until metered
billing).

The event log alone would make `usage()` a `COUNT(*)` over a table that grows without bound, on
the hot path of every AI call. The rollup is one row per account per billing period.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned

__all__ = ["AIUsageEvent", "AIUsageRollup"]


class AIUsageEvent(Base, TenantOwned):
    """One row per **user-initiated** AI call (D11, `PRICING` §5.2).

    System-initiated calls are not recorded and not counted — N10: *"a limit that trips a
    scheduled job is a bug: the user cannot upgrade their way out of something they did not
    do."* The nightly recurring-task sweep and the weather job dispatch to a provider like
    anything else, and neither should consume a household's quota.
    """

    __tablename__ = "ai_usage_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Which dispatch path: "web.agent", "cli.ai", "gateway.telegram", … .
    #
    # **Not used for billing** — used to prove at audit time that every entry point is metered
    # (A11). `PRICING` §5.1 bills *calls*, so the count is the billing unit and this column is
    # the evidence that the count is complete.
    entry_point: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)  # complete|structured_output|stream

    # Nullable because this is telemetry, not the billing unit: §5.1 meters calls rather than
    # tokens, and some providers return no usage at all. A NULL here is "not reported", which is
    # different from zero and must stay distinguishable.
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AIUsageEvent {self.entry_point} {self.method}>"


class AIUsageRollup(Base, TenantOwned):
    """The materialized monthly counter — the number `usage()` actually returns (D18).

    Incremented in the **same transaction** as its event row, so two concurrent calls at the cap
    cannot both pass (`PRICING` §3.2 rule 5). That rule is why the increment is not a separate
    write: a counter updated after the fact is a counter two requests can read stale.
    """

    __tablename__ = "ai_usage_rollups"
    __table_args__ = (
        # One row per account per period, and the constraint is load-bearing rather than
        # descriptive: `record_usage` inserts-then-increments, and this is what makes the
        # concurrent-insert case resolvable instead of producing two half-counters.
        UniqueConstraint(
            "account_id", "period_start", name="uq_ai_usage_rollup_account_period"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )

    # First day of the **billing** month — not the calendar month when a subscription exists.
    # §5.1 resets on the billing anniversary, so a Pro customer who subscribed on the 20th resets
    # on the 20th. Free accounts have no anniversary and use the calendar month.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Set when the 80% / 100% nudges are sent, so each fires **once** per period (§5.3). Columns
    # rather than a derived check because "have we already told them" is a fact about what was
    # sent, and recomputing it from `calls_used` would re-send on every call past the threshold.
    warned_80_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    warned_100_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AIUsageRollup {self.period_start} calls={self.calls_used}>"
