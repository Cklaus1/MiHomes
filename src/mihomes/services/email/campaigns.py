"""Drip campaigns — enrolment, scheduling, and the `drips` job (SPEC-005 §6 Step 11, A25).

**Mechanism only. The content is O1** — a founder decision about what a stranger reads in their
first week, and a wrong guess is not a bug, it is a bad first impression sent to every new
customer. What is built here is everything around that: enrolment, the schedule, suppression,
unsubscribe, and the completion rules.

## The sequences are a module constant, not config rows

`configurations` is a **tenant** table, and a drip sequence is product-wide: storing it there
would mean a row per account, drifting apart silently, with no answer to "what does the onboarding
sequence say" that is true for everyone. So the sequences are declared here as data with
placeholder templates, the same shape as `BACKOFF_LADDER` and the dunning `LADDER`.

O1 replaces the template names and the intervals. **Nothing about the mechanism changes when it
does** — which is what makes the openness harmless (conventions §3.3), and what A25 proves by
asserting the machinery against whatever `SEQUENCES` says rather than against fixed values.

## A shortened sequence completes; it does not error

§4.2 is explicit, and the reason is operational: O1 may shorten a sequence *after* enrolments
exist. An enrolment whose `step` is past the new end is **completed** — the customer has had
everything there is to send. Raising instead would turn an ordinary content edit into a nightly
job that fails for exactly the accounts furthest along, which is the worst possible selection.

## `completed_at` is the idempotency guarantee

Non-NULL means the scheduler skips the row forever, whether the sequence finished or the account
unenrolled. "Send each step once and never twice" (A25) rests on that column plus the unique
constraint — not on the job remembering what it did on a previous run.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mihomes.models.email_campaign import CampaignEnrolment

__all__ = [
    "ONBOARDING",
    "SEQUENCES",
    "complete",
    "due_sends",
    "enrol",
    "send_due_steps",
    "sequence_length",
]

logger = logging.getLogger(__name__)

#: The onboarding sequence: `(template, delay from enrolment)`.
#:
#: **Placeholder content (O1).** The template names and intervals are the founder's call; the
#: fixture templates below exist so the machinery is testable and shippable before that decision
#: lands. `drip_onboarding_1` renders today — a shipped sequence that says something useful is
#: better than a mechanism with nothing wired to it, and the copy is a config edit away.
ONBOARDING: tuple[tuple[str, timedelta], ...] = (
    ("drip_onboarding_1", timedelta(days=1)),
    ("drip_onboarding_2", timedelta(days=4)),
    ("drip_onboarding_3", timedelta(days=10)),
)

#: Campaign name -> sequence. `enrol` accepts any key here and nothing else, so a typo at a call
#: site fails immediately rather than creating an enrolment that can never come due.
SEQUENCES: dict[str, tuple[tuple[str, timedelta], ...]] = {
    "onboarding": ONBOARDING,
}


def sequence_length(campaign: str) -> int:
    """How many steps this campaign has today. **Read live, never cached on the row.**

    An enrolment stores its step *index*, so shortening a sequence changes what "finished" means
    for rows that already exist — which is exactly what §4.2 requires and what a stored length
    would defeat.
    """
    return len(SEQUENCES.get(campaign, ()))


def enrol(
    session: Session, account_id, campaign: str, *, now: datetime | None = None
) -> CampaignEnrolment | None:
    """Enrol an account. Returns the row, or `None` if it was already enrolled.

    **Insert-first, not check-then-insert.** The unique constraint on
    `(account_id, campaign)` is the guard — §5.3 says so directly — and two concurrent
    enrolments both see "not present" and both insert. G6's
    `test_the_enrolment_uniqueness_survives_the_migration` is what keeps that constraint in the
    shipped DDL rather than only in the model.
    """
    if campaign not in SEQUENCES:
        raise ValueError(f"unknown campaign {campaign!r}; expected one of {sorted(SEQUENCES)}")

    row = CampaignEnrolment(
        account_id=account_id,
        campaign=campaign,
        enrolled_at=now or datetime.now(UTC),
        step=0,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        logger.info("already enrolled: campaign=%s", campaign)
        return None
    return row


def _next_due_at(enrolment: CampaignEnrolment) -> datetime | None:
    """When this enrolment's next step is due, or `None` if the sequence is finished."""
    sequence = SEQUENCES.get(enrolment.campaign, ())
    if enrolment.step >= len(sequence):
        return None
    _template, delay = sequence[enrolment.step]
    return enrolment.enrolled_at + delay


def due_sends(session: Session, *, now: datetime) -> list[CampaignEnrolment]:
    """Enrolments whose next step is due.

    `now` is injected (N11), so the schedule is testable without sleeping — a sequence measured
    in days cannot be exercised against a real clock, and a test that waits is a test that gets
    deleted.

    **An enrolment past the end of a shortened sequence is completed here, not returned.** That
    is §4.2's rule applied at the point the scheduler asks, so a content edit resolves itself on
    the next run rather than needing a migration or a cleanup script.
    """
    open_rows = list(
        session.execute(
            sa.select(CampaignEnrolment).where(CampaignEnrolment.completed_at.is_(None))
        ).scalars()
    )

    due = []
    for row in open_rows:
        due_at = _next_due_at(row)
        if due_at is None:
            # Finished — either it reached the end, or O1 shortened the sequence underneath it.
            complete(session, row, now=now)
            continue
        if due_at <= now:
            due.append(row)
    return due


def complete(session: Session, enrolment: CampaignEnrolment, *, now: datetime) -> None:
    """Mark an enrolment finished. Idempotent; a completed row is skipped forever."""
    if enrolment.completed_at is None:
        enrolment.completed_at = now
        session.flush()


def send_due_steps(session: Session, email_service, *, now: datetime) -> int:
    """Send every due step for this account, advancing each enrolment. Returns how many.

    Advances `step` **in the same transaction as the enqueue**, which is what makes "never
    twice" true across a crash: a run that enqueued and then died without advancing would
    re-send on the next pass, and the customer would receive step 1 twice.
    """
    sent = 0
    for enrolment in due_sends(session, now=now):
        sequence = SEQUENCES[enrolment.campaign]
        template, _delay = sequence[enrolment.step]

        email_service._send(
            _recipient(session, enrolment.account_id),
            template,
            {"campaign": enrolment.campaign, "step": enrolment.step + 1},
            # Lifecycle — a drip IS marketing, which is the half of D13 that makes the
            # transactional/lifecycle split coherent. Suppression applies absolutely, and the
            # RFC 8058 headers go on (A18).
            klass="lifecycle",
        )

        enrolment.step += 1
        enrolment.last_sent_at = now
        if enrolment.step >= len(sequence):
            complete(session, enrolment, now=now)
        session.flush()
        sent += 1

    return sent


def _recipient(session: Session, account_id) -> str:
    """The owner's address. Reuses billing's resolver rather than a second lookup.

    One definition of "the account's email", so a drip and an invoice cannot disagree about who
    the customer is — and it already handles the revoked-owner case that a naive query misses.
    """
    from mihomes.models.account import Account
    from mihomes.services.billing.service import _billing_email

    return _billing_email(session, session.get(Account, account_id))
