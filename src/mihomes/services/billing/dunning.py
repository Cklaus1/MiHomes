"""The dunning ladder — `payment_failed` starts a sequence (SPEC-005 §6 Step 10, A23/A24).

Phase 3 sends **one** `payment_failed` email and stops (SPEC-004 B2). This is the escalating
remainder: three further rungs on a schedule, each one more direct than the last, and all of them
cancelled the moment the customer pays.

## No new table — the outbox already is one

`EmailOutbox` carries `next_attempt_at`, `klass`, `template` and `context`: a row due in seven days
is a scheduled send, which is what a dunning rung is. So `start_ladder` enqueues four rows at
once — one due now, three at the configured offsets — and `drain-outbox` sends each as it comes
due. A23 becomes close to structural rather than something the ladder has to enforce.

§4.4's five tables are all shipped and none of them is a sequence table, which is the same
conclusion read from the other direction; N12 forbids adding one to `accounts`.

**`next_attempt_at` does double duty, deliberately.** It is also the backoff ladder's field, so a
rung due in seven days that then fails delivery is rescheduled to +1 minute by `drain`. That
composes correctly — dunning picks the *first* attempt, backoff owns retries after it — but the
two schedules share a column and a reader could take them for a conflict.

## Transactional, not lifecycle — and §5.2 says otherwise

§5.2 lists `send_dunning` under *"Lifecycle mail — every one of these is `klass="lifecycle"`"*.
**This ships transactional**, which contradicts that grouping and follows D13's stated principle
instead: *"a receipt for money taken is not marketing and must send regardless of unsubscribe
state; a drip is, and must not."*

A dunning notice is the first kind. The discriminating question is whether a suppressed address
needs rungs 2–4, and it does: under D13 suppression is **absolute** for lifecycle mail, so an
unsubscribed customer would be told once that their card failed and then silenced while their
access lapsed. A18 would also put `List-Unsubscribe` on "your payment failed", which is an
invitation to opt out of a warning rather than an offer to stop marketing.

It also has to match rung 1. SPEC-004's `send_payment_failed` was classified transactional at
SPEC-005 G2 with the reason recorded inline, and rungs 2–4 are that same message escalating about
the same unpaid invoice. One sequence, one class — the two cannot differ.

## The cadence is a config value

`BILLING` §10 lists dunning policy as an **open question** — *"how many retries / how long in
`past_due` grace before moving to `unpaid`?"* — so the offsets below are a default, not a fact
about the product. The mechanism is provable either way, which is what makes the openness
harmless (conventions §3.3): A23 asserts the rungs land where `LADDER` says, whatever it says.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from mihomes.models.email_outbox import EmailOutbox
from mihomes.services.email.outbox import enqueue

__all__ = ["CANCELLED_MARKER", "LADDER", "cancel_ladder", "pending_rungs", "start_ladder"]

logger = logging.getLogger(__name__)

#: `(template, offset from the failure)`. Rung 1 is due immediately — it is SPEC-004's
#: `payment_failed`, kept in the ladder so the whole sequence is one object rather than one
#: email plus a follow-up mechanism.
#:
#: **Written out, and asserted rung by rung** (A23). "Four rows exist" passes with all four due
#: at once, which is a very different product: four identical-looking warnings in one minute
#: reads as a bug to the customer and as spam to their mailbox provider.
LADDER: tuple[tuple[str, timedelta], ...] = (
    ("payment_failed", timedelta(0)),
    ("dunning_2", timedelta(days=3)),
    ("dunning_3", timedelta(days=7)),
    ("dunning_final", timedelta(days=14)),
)

#: Written to `last_error` on a rung cancelled by recovery.
#:
#: `sent_at` is stamped because the row must stop being selected, and the outbox's rule is that a
#: dead row is **kept** — "why did the customer not get the final notice" is a support question,
#: and a deleted row answers it with silence. The marker is what distinguishes "we chose not to
#: send this" from "this was sent", which `sent_at` alone cannot. Same shape as the drain's
#: `"suppressed at send time"`.
CANCELLED_MARKER = "cancelled: payment recovered"


def start_ladder(
    session: Session,
    *,
    to: str,
    account_id,
    plan: str,
    billing_url: str,
    now: datetime | None = None,
    grace_days: int | None = None,
) -> list[EmailOutbox]:
    """Enqueue the whole sequence. Rung 1 is due now; the rest on the `LADDER` schedule.

    Idempotent per unpaid invoice by construction of the caller: the webhook handler calls this
    once per `invoice.payment_failed`, and Stripe's own retries are deduplicated upstream by the
    webhook ledger (SPEC-004 N4). A second call here would genuinely mean a second failure.
    """
    now = now or datetime.now(UTC)
    rows = []

    for step, (template, offset) in enumerate(LADDER, start=1):
        rows.append(
            enqueue(
                session,
                to=to,
                template=template,
                context={
                    "plan": plan,
                    "billing_url": billing_url,
                    "grace_days": grace_days,
                    "step": step,
                },
                # Transactional — see the module docstring on why this departs from §5.2.
                klass="transactional",
                account_id=account_id,
                now=now + offset,
            )
        )

    logger.info("dunning ladder started: account=%s rungs=%d", account_id, len(rows))
    return rows


def pending_rungs(session: Session, account_id) -> list[EmailOutbox]:
    """Unsent ladder rows for this account — what a recovery would cancel."""
    templates = [template for template, _ in LADDER]
    return list(
        session.execute(
            sa.select(EmailOutbox).where(
                EmailOutbox.account_id == account_id,
                EmailOutbox.template.in_(templates),
                EmailOutbox.sent_at.is_(None),
                EmailOutbox.failed_at.is_(None),
            )
        ).scalars()
    )


def cancel_ladder(
    session: Session, account_id, *, now: datetime | None = None
) -> int:
    """Stop the sequence. Returns how many rungs were cancelled (A24).

    Called on recovery — `invoice.paid`, or a subscription that leaves `past_due`. **The rungs
    that already went out are not recalled**; what stops is everything still queued, which is the
    only part still in our control and the part the customer would experience as being nagged
    after they had already paid.
    """
    now = now or datetime.now(UTC)
    cancelled = 0

    for row in pending_rungs(session, account_id):
        row.sent_at = now
        row.last_error = CANCELLED_MARKER
        cancelled += 1

    if cancelled:
        session.flush()
        logger.info(
            "dunning ladder cancelled: account=%s rungs=%d", account_id, cancelled
        )
    return cancelled
