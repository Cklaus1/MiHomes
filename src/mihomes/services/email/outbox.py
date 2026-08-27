"""The email outbox — `enqueue`, `drain`, and the backoff ladder (SPEC-005 §5.3, D12).

`EmailService._send` enqueues; nothing in business code calls the provider directly (N2). A
direct send inside a web request reintroduces exactly the coupling `BILLING` §2.4 forbids: a slow
provider becomes a slow page, and a failed provider becomes a failed checkout.

## `drain` is per account, not global

The spec's signature is `drain(session, *, limit=100, now)` — one sweep over every due row. Under
RLS that returns **zero rows**: with no account bound the policy predicate is `account_id = NULL`,
which is NULL rather than true, for every row. Measured, not assumed.

So the caller binds an account and drains it, and the CLI job iterates accounts — the pattern
`cli/jobs.py` already uses for `reconcile` and `trial-sweep`. `drain_all` is that loop, and it is
where G5's `mihomes jobs drain-outbox` will point.

## Rendering happens here, not at enqueue

So a template fix repairs mail that is already queued (§4.1). The row carries the render context;
`drain` renders it at send time. See `models/email_outbox.py` on the spec contradiction this
resolves.

## Idempotent and safe to run twice (D9)

`drain` selects only rows that are unsent, unfailed and due. A row it sends is stamped `sent_at`
in the same transaction, so a second consecutive run finds nothing — which is what makes A17's
"no-op on a second run" true by construction rather than by a guard.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.email_outbox import BACKOFF_LADDER, MAX_ATTEMPTS, EmailOutbox
from mihomes.services.email.provider import EmailProvider, EmailSendError
from mihomes.services.email.render import render_template
from mihomes.services.email.suppression import is_suppressed
from mihomes.tenancy.context import require_account

__all__ = ["DrainResult", "drain", "enqueue", "next_attempt_after"]

logger = logging.getLogger(__name__)


@dataclass
class DrainResult:
    """What one `drain` did. Counts rather than rows, so it is cheap to log."""

    sent: int = 0
    failed: int = 0        # attempts that failed and were rescheduled
    exhausted: int = 0     # rows that hit MAX_ATTEMPTS and were marked failed
    suppressed: int = 0    # lifecycle rows whose address was suppressed before sending
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.sent + self.failed + self.exhausted + self.suppressed


def next_attempt_after(attempts: int, now: datetime) -> datetime | None:
    """When to retry after `attempts` failures, or `None` when the ladder is exhausted.

    `attempts` is the count **after** incrementing, so `next_attempt_after(1, now)` is the wait
    following the first failure. Returns `None` at `MAX_ATTEMPTS`, which is what makes the fifth
    failure terminal rather than scheduled — see `BACKOFF_LADDER` on why the spec's five
    intervals do not correspond to five attempts.
    """
    if attempts >= MAX_ATTEMPTS:
        return None
    return now + BACKOFF_LADDER[attempts - 1]


def enqueue(
    session: Session,
    *,
    to: str,
    template: str,
    context: dict,
    klass: str,
    account_id,
    now: datetime,
) -> EmailOutbox:
    """Insert a due-now row. Called by `EmailService._send`, never by business code (N2)."""
    row = EmailOutbox(
        account_id=account_id,
        to_address=to,
        template=template,
        context=json.dumps(context, default=str),
        klass=klass,
        attempts=0,
        next_attempt_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _due(session: Session, *, limit: int, now: datetime) -> list[EmailOutbox]:
    """This account's unsent, unfailed, due rows — oldest first.

    All three conditions matter. Dropping `sent_at IS NULL` re-sends delivered mail on the next
    run; dropping `failed_at IS NULL` retries dead rows forever (A16's "stops being selected");
    dropping the `next_attempt_at` bound ignores the backoff entirely and hammers a provider
    that is already failing.
    """
    stmt = (
        select(EmailOutbox)
        .where(
            EmailOutbox.sent_at.is_(None),
            EmailOutbox.failed_at.is_(None),
            EmailOutbox.next_attempt_at <= now,
        )
        .order_by(EmailOutbox.next_attempt_at)
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def drain(
    session: Session,
    provider: "EmailProvider | Callable[[], EmailProvider]",
    *,
    limit: int = 100,
    now: datetime,
    record_delivery=None,
) -> DrainResult:
    """Send this account's due rows, oldest first. Idempotent and safe to run twice (D9).

    `now` is injected rather than read from the clock, so the ladder is testable without
    sleeping (N11) — no test in this suite sleeps.

    `record_delivery(row, result)` is called after each successful send. Injected rather than
    imported so the delivery write stays `EmailService`'s concern: the outbox's job is delivery
    attempts, and A19's "exactly one row per send" is satisfied by this being the only place a
    successful send happens once Step 4 lands.
    """
    result = DrainResult()

    # A zero-arg callable is a *factory*, resolved on first real send — so a run with nothing
    # due never constructs a provider and never needs its credentials. An object with `.send`
    # is used directly, which is what every test passes.
    resolve = provider if callable(provider) and not hasattr(provider, "send") else lambda: provider

    for row in _due(session, limit=limit, now=now):
        # Suppression is re-checked at send time, not trusted from enqueue time. An address
        # can be suppressed while mail sits in the queue — a bounce webhook, an unsubscribe
        # click — and sending anyway because "it was fine when we queued it" is exactly the
        # complaint that gets a sending domain blocklisted.
        if row.klass == "lifecycle" and is_suppressed(session, row.to_address):
            row.sent_at = now
            row.last_error = "suppressed at send time"
            result.suppressed += 1
            logger.info("outbox: suppressed at send time (template=%s)", row.template)
            continue

        try:
            context = json.loads(row.context)
            subject, html, text = render_template(row.template, context)
        except Exception as exc:
            # A render fault is our bug and no amount of retrying fixes it, so the row is
            # marked failed immediately rather than walking the whole ladder to reach the
            # same conclusion twelve hours later.
            row.failed_at = now
            row.last_error = f"render failed: {exc}"
            result.exhausted += 1
            logger.exception("outbox: render failed (template=%s)", row.template)
            continue

        try:
            send_result = resolve().send(row.to_address, subject, html, text=text)
        except EmailSendError as exc:
            row.attempts += 1
            row.last_error = str(exc)
            retry_at = next_attempt_after(row.attempts, now)
            if retry_at is None:
                row.failed_at = now
                result.exhausted += 1
                logger.error(
                    "outbox: giving up after %s attempts (template=%s)",
                    row.attempts, row.template,
                )
            else:
                row.next_attempt_at = retry_at
                result.failed += 1
                logger.warning(
                    "outbox: attempt %s failed, retrying at %s (template=%s)",
                    row.attempts, retry_at, row.template,
                )
            continue

        row.attempts += 1
        row.sent_at = now
        row.last_error = None
        result.sent += 1
        if record_delivery is not None:
            try:
                record_delivery(row, send_result)
            except Exception:
                # The message has already left. Losing the record of it is bad; letting that
                # loss abort the drain — stranding every message behind this one — is worse,
                # and no retry can un-send what went out. `_record_delivery` guards itself
                # too, but the callback is injected, so the guard belongs at the call site
                # as well: a caller that passes a raising callback must not break delivery.
                logger.exception(
                    "outbox: failed to record delivery (template=%s)", row.template
                )

    session.flush()
    return result


def drain_all(
    session_factory,
    provider_factory,
    *,
    limit: int = 100,
    now: datetime,
    record_delivery=None,
) -> DrainResult:
    """Drain every account, binding tenant context per account.

    The cross-account half of the sweep, kept out of `drain` so that a caller who already has
    an account bound — a request handler, a test — never accidentally iterates the estate.

    **One account's failure must not abort the sweep** (`cli/jobs.py`'s own rule): a single bad
    row would otherwise leave every account after it undrained, and the symptom would be "mail
    stopped going out" long after the cause.
    """
    from mihomes.models.account import Account
    from mihomes.tenancy.context import account_context

    combined = DrainResult()

    with session_factory() as session:
        account_ids = list(session.execute(select(Account.id)).scalars())

    # **Constructed once, and only when there is something to send.**
    #
    # Two bugs, both found by running `mihomes jobs drain-outbox` rather than by testing
    # `drain`. Calling `provider_factory()` per account meant a missing `RESEND_API_KEY`
    # produced one error *per account* — N identical tracebacks, none of which said the real
    # problem — and it meant an install with an empty queue could not run the job at all.
    #
    # An empty queue needing no credentials is the case that matters: until a sending domain
    # is verified (§0.8 U7) that is every install, and a nightly cron line failing with
    # "RESEND_API_KEY is not set" trains an operator to ignore this job's mail.
    provider_holder: list = []

    def provider():
        if not provider_holder:
            provider_holder.append(provider_factory())
        return provider_holder[0]

    for account_id in account_ids:
        try:
            with account_context(account_id), session_factory() as session:
                one = drain(
                    session,
                    provider,
                    limit=limit,
                    now=now,
                    record_delivery=record_delivery,
                )
                session.commit()
            combined.sent += one.sent
            combined.failed += one.failed
            combined.exhausted += one.exhausted
            combined.suppressed += one.suppressed
        except Exception as exc:
            combined.errors.append(f"{account_id}: {exc}")
            logger.exception("outbox: drain failed for account %s", account_id)

    return combined


def current_account_id():
    """The bound account, for callers that need to enqueue. Raises if unset."""
    return require_account()
