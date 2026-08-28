"""`mihomes jobs` — the scheduled work Phase 3 is the first phase to need (D15, F1).

`MULTITENANCY` §11.4 named both jobs and deferred the trigger: *"Decide when Phase 3 schedules
land; it does not block Phase 1."* It does block Phase 3 — the card-less trial has **no other
trigger** (F3: with no card there is no Stripe subscription, so no `trial_will_end` webhook), and
one of the four emails is this sweep's own output.

## The interface is the load-bearing half, not the trigger

D15: *"a command that is safe to run twice is testable with no scheduler present and swappable if
the platform's capability differs from what is assumed here."* Fly's scheduled-machine mechanism
has **not** been verified against their documentation, so the deployment shape is a default with a
named alternative (a dedicated always-on machine), not an asserted fact. What is asserted is that
running either command twice in a row is a no-op the second time (A16).

## Both jobs sweep *across* accounts, which is why they bind context per account

Every other CLI command binds one tenant for the whole invocation (`cli/__init__.py::_bind_account`)
and every write stamps `account_id` from that ContextVar. A sweep cannot do that — it visits every
account with a Stripe customer — so it binds and unbinds per account inside the loop. The accounts
list itself is read with **no** context bound, which is possible only because `accounts` is the
tenant root and carries no RLS policy of its own.

**One account's failure must not abort the sweep.** A single unreachable Stripe customer, or one
corrupted row, would otherwise leave every account after it in the list unreconciled — and the
symptom would be "reconciliation stopped working" long after the cause. Errors are logged per
account and the sweep continues, which is also what makes the command safe to re-run.
"""

from __future__ import annotations

import logging

import typer
from rich import print as rprint

logger = logging.getLogger(__name__)

app = typer.Typer(name="jobs", help="Scheduled maintenance jobs (billing, trials)")


# ── SPEC-005 §6 Step 5 — the four Phase 4 workloads ────────────────────────
#
# **The scheduler is this phase's definition of done** (A15). The spec's own framing:
#
#   > A scheduler that never fires is indistinguishable from a system with nothing to do. The
#   > trial sweep, the reconciliation sweep, the outbox drain, the dunning ladder, the drips and
#   > the weekly digest are six workloads with one trigger. If that trigger is misconfigured,
#   > every one of them stops, no exception is raised, no test fails, and the first signal is a
#   > customer asking why they were never told their card failed.
#
# So `SCHEDULE` below is data, and `test_jobs_enumeration.py` derives its check from the Typer
# app rather than from a list a human maintains. **That gate has already caught something**: at
# the time it was written, `mihomes cron setup` — the only place this repo tells an operator
# what to schedule — listed four commands, none of which was `reconcile` or `trial-sweep`.
# SPEC-004 added both and neither reached the manifest, which is exactly the silent drift A15
# describes, having already happened once before the criterion existed to catch it.


#: Every `jobs` workload, with the cadence an operator should schedule it at.
#:
#: **The single source of truth for scheduling.** `mihomes cron setup` prints from this, and
#: `test_all_workloads_scheduled` walks the Typer app and asserts every registered command
#: appears here — so a seventh workload added without a cadence fails the suite rather than
#: silently never running.
#:
#: Cadences are defaults, not assertions about the deployment: SPEC-004 D15's assumption about
#: Fly's scheduled-machine mechanism is **still unverified** (§0.8 U10), and what is asserted
#: here is that each command is idempotent, so running it more or less often than this is safe.
SCHEDULE: dict[str, tuple[str, str]] = {
    # name: (cron expression, why this cadence)
    "drain-outbox": (
        "*/5 * * * *",
        "Queued mail should leave within minutes; the backoff ladder handles a provider "
        "outage, so a frequent drain costs nothing when there is nothing to send.",
    ),
    "dunning": (
        "0 10 * * *",
        "The BILLING §5 ladder is measured in days. Once daily, mid-morning, so a customer "
        "reads 'your card failed' at a time they can act on it.",
    ),
    "drips": (
        "0 9 * * *",
        "Drip steps are scheduled in days (O1 sets the content and intervals). Daily is the "
        "finest granularity the sequence can use.",
    ),
    "weekly-digest": (
        "0 8 * * 1",
        "Monday morning, matching `mihomes ai review`'s existing weekly slot. D16: Estate buys "
        "the schedule, not the feature.",
    ),
    "trial-sweep": (
        "0 6 * * *",
        "The card-less trial's ONLY clock (F3). Daily, early, so an expiry is acted on the day "
        "it happens rather than up to a week late.",
    ),
    "reconcile": (
        "0 3 * * *",
        "The backstop for a dropped webhook (BILLING §6). Nightly and off-peak: it re-fetches "
        "from the provider, so it is the most expensive of the six and the least urgent.",
    ),
}


@app.command("drain-outbox")
def drain_outbox(
    limit: int = typer.Option(
        100, "--limit", help="Maximum messages to send per account in one run."
    ),
) -> None:
    """Send queued mail. **The only path from the outbox to a provider** (D12/N2).

    Idempotent by construction (A17): a sent row is stamped `sent_at` in the same transaction,
    so a second consecutive run selects nothing. Nothing needs to remember that it already ran.

    Sweeps per account for the same reason `reconcile` does, and for one more: an account-less
    read of a tenant table returns **zero rows** under RLS — measured — so a global drain would
    report success having sent nothing at all.
    """
    from datetime import UTC, datetime

    from mihomes.db import get_session
    from mihomes.services.email import get_email_provider
    from mihomes.services.email.outbox import drain_all

    result = drain_all(
        get_session,
        get_email_provider,
        limit=limit,
        now=datetime.now(UTC),
    )

    rprint(
        f"Drained {result.sent} message(s); {result.failed} rescheduled; "
        f"{result.exhausted} gave up; {result.suppressed} suppressed."
    )
    for err in result.errors:
        logger.error("drain-outbox: %s", err)


@app.command("dunning")
def dunning(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be sent without sending it."
    ),
) -> None:
    """Advance the dunning ladder for accounts whose payment failed.

    **Step 10 fills this in.** Registered here because Step 5 must come first: the spec makes
    the ordering load-bearing (*"Step 5 before Steps 10, 11 and 13"*), and a workload whose
    command does not exist cannot be scheduled, tested for idempotence, or enumerated by A15.

    Deliberately a no-op that reports zero rather than a `NotImplementedError`: a scheduler
    calling this before Step 10 lands must not page anyone.
    """
    rprint("Dunning ladder: 0 account(s) advanced. (Step 10 wires the ladder.)")
    if dry_run:
        rprint("[dim]--dry-run had no effect: nothing is sent yet.[/dim]")


@app.command("drips")
def drips(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be sent without sending it."
    ),
) -> None:
    """Send due drip-campaign steps (SPEC-005 Step 11).

    Sweeps per account for the same reason every other workload does: an account-less read of a
    tenant table returns zero rows under RLS, so a global pass would report success having sent
    nothing.

    Mail is **enqueued**, not sent — `drain-outbox` delivers it. So this job is idempotent by
    the same mechanism as everything else: a step advances the enrolment in the same transaction
    as the enqueue, and a second run finds nothing due.
    """
    from datetime import UTC, datetime

    from mihomes.services.email import get_email_provider
    from mihomes.services.email.campaigns import send_due_steps
    from mihomes.services.email.service import EmailService

    now = datetime.now(UTC)
    advanced = failed = 0

    for account_id, _ in _all_accounts():
        try:
            with _account_session(account_id) as (session, _account):
                if dry_run:
                    from mihomes.services.email.campaigns import due_sends

                    advanced += len(due_sends(session, now=now))
                    continue
                advanced += send_due_steps(
                    session, EmailService(get_email_provider(), session=session), now=now
                )
                session.commit()
        except Exception:
            failed += 1
            logger.exception("drips: failed for account %s", account_id)

    verb = "would advance" if dry_run else "advanced"
    rprint(f"Drips: {verb} {advanced} enrolment(s); {failed} failed.")


@app.command("weekly-digest")
def weekly_digest(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report who would receive it without sending."
    ),
) -> None:
    """Send the weekly AI digest to accounts whose plan includes it.

    **Step 13 fills this in**, after Step 12's gate exists. D16 enforces this as a *send*, not
    a gate: the on-request route stays available on every plan (A14b), and what Estate buys is
    the schedule.
    """
    rprint("Weekly digest: 0 account(s) sent. (Step 13 wires the digest.)")
    if dry_run:
        rprint("[dim]--dry-run had no effect: nothing is sent yet.[/dim]")


@app.command("reconcile")
def reconcile(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report drift without correcting it."
    ),
) -> None:
    """Re-fetch subscription state from the provider and correct local drift.

    **The backstop for webhooks failing eventually** (`BILLING` §6). Webhooks are the source of
    truth (D1), and they are also delivered over a network by a third party: one dropped event
    leaves an account on the wrong plan indefinitely, because nothing else would ever revisit it.

    Idempotent by construction — it re-fetches and compares, so a second consecutive run finds
    nothing to change and reports zero corrections (A16). That is not a defensive property bolted
    on; it is what "reconcile" means.
    """
    from mihomes.services.billing.provider import BillingProviderError, get_billing_provider
    from mihomes.services.billing.service import apply_subscription_state

    accounts = _accounts_with_customers()

    # **Nothing to reconcile is success, not a configuration error.**
    #
    # The provider was constructed before this check, so on an install with no Stripe
    # customers — which is every install until the founder sets Stripe up (§0.8 U6) — a
    # nightly `mihomes jobs reconcile` exited 1 with "STRIPE_SECRET_KEY is not set". Cron
    # mails that failure every night, and an operator learns to filter this job's mail.
    #
    # Found by A17 invoking the command, not by testing the sweep: every service-layer test
    # passed, because they construct a provider themselves.
    if not accounts:
        rprint("Reconciled 0 account(s); corrected 0; 0 failed.")
        return

    try:
        provider = get_billing_provider("stripe")
    except BillingProviderError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    checked = corrected = failed = 0
    for account_id, customer_id in accounts:
        checked += 1
        try:
            state = provider.get_subscription(customer_id=customer_id)
            if dry_run:
                continue
            with _account_session(account_id) as (session, account):
                if apply_subscription_state(session, account, state):
                    corrected += 1
                    logger.info("reconcile: corrected drift on account %s", account_id)
        except Exception:
            # Per-account, so one bad customer does not strand every account after it.
            failed += 1
            logger.exception("reconcile: failed for account %s", account_id)

    verb = "would correct" if dry_run else "corrected"
    rprint(f"Reconciled {checked} account(s); {verb} {corrected}; {failed} failed.")


@app.command("trial-sweep")
def trial_sweep(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would expire without changing anything."
    ),
) -> None:
    """Expire finished trials and flag the ones ending soon.

    **The trial's only clock.** `PRICING:143`: *"with no card there is no Stripe subscription
    during the trial … the `trial_ending` email is triggered by **our scheduler**, not Stripe's
    `trial_will_end` webhook."* The handler for that webhook exists (Step 5) and is unreachable
    while the trial is card-less — a deliberate piece of room, not dead code (§7's deferred list).

    Step 13 lands the state machine this drives; today it reports and expires, so the entrypoint
    is testable before the thing it triggers exists. That ordering is D15's point: the command is
    verifiable with no scheduler and no trial.
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    expired = ending_soon = 0

    for account_id, _customer_id in _accounts_with_trials():
        with _account_session(account_id) as (session, account):
            if account.trial_ends_at is None:
                continue
            remaining = (account.trial_ends_at - now).days
            if account.trial_ends_at <= now:
                expired += 1
                if not dry_run:
                    _expire_trial(session, account)
            elif remaining <= TRIAL_ENDING_WINDOW_DAYS:
                # Step 15 sends the email; this counts what it will send to. Split so a failure
                # in the mail path cannot mark a trial as notified without notifying anyone.
                ending_soon += 1

    verb = "would expire" if dry_run else "expired"
    rprint(f"Trial sweep: {verb} {expired}; {ending_soon} ending within "
           f"{TRIAL_ENDING_WINDOW_DAYS} days.")


#: How far ahead the sweep flags a trial as ending.
#:
#: `PRICING` §4.3: *"show the home-picker **~3 days before expiry**, alongside the `trial_ending`
#: email, so the choice is made before access changes rather than after."* Three days is the
#: product's number, not an arbitrary window — the point is that the user chooses which home stays
#: active while they still have access to all of them.
TRIAL_ENDING_WINDOW_DAYS = 3


def _expire_trial(session, account) -> None:
    """End a trial: back to Free, `trial_ends_at` cleared, `trial_used_at` **kept**.

    `trial_used_at` is what enforces *"one trial per account, ever"* (A18), so clearing it on
    expiry would hand every account an unlimited supply of trials — the quietest possible way to
    give the product away. Step 13 owns the full state machine; this is the half the sweep needs.

    **Nothing is deleted.** An account that ran four homes on trial keeps all four, over-limit and
    read-only per `PRICING` §4.3 — the same non-destructive shape as a voluntary downgrade.

    **`subscription_status` must be cleared too**, and the first version of this function did not
    — a Step 13 gap found by writing A19's test. `_STATUS_TO_EFFECTIVE_PLAN` maps `trialing` to
    *"the account's own plan"*, so an expired account left at `trialing` would resolve against
    whatever `plan` says. It reverts to `free` here, so nothing leaked; but the moment anything
    read the status directly — a banner, a webhook comparison, the reconcile sweep — it would have
    said an ended trial was still running.
    """
    account.plan = "free"
    account.subscription_status = None
    account.trial_ends_at = None
    session.commit()
    logger.info("trial expired for account %s", account.id)


def _all_accounts() -> list[tuple[object, str | None]]:
    """Every account. The drip sweep visits all of them, not only paying ones.

    `_accounts_with_customers` and `_accounts_with_trials` are billing-shaped filters; an
    onboarding drip is for people who have just signed up and therefore have neither.
    """
    from sqlalchemy import select

    from mihomes.db import get_session
    from mihomes.models.account import Account

    with get_session() as session:
        return list(
            session.execute(select(Account.id, Account.stripe_customer_id)).all()
        )


def _accounts_with_customers() -> list[tuple[object, str]]:
    """`(account_id, stripe_customer_id)` for every account that has one.

    Read with **no account context bound**, which works only because `accounts` is the tenant root
    (SPEC-002: it has no `account_id` of its own — it is what `account_id` points at). Returned as
    plain tuples rather than ORM objects so the session can close: the sweep then opens one
    scoped session per account, and a detached instance would raise on first attribute access.
    """
    from sqlalchemy import select

    from mihomes.db import get_session
    from mihomes.models.account import Account

    with get_session() as session:
        return list(
            session.execute(
                select(Account.id, Account.stripe_customer_id).where(
                    Account.stripe_customer_id.is_not(None)
                )
            ).all()
        )


def _accounts_with_trials() -> list[tuple[object, str | None]]:
    """Every account with a trial end date set — including past ones, which are the point."""
    from sqlalchemy import select

    from mihomes.db import get_session
    from mihomes.models.account import Account

    with get_session() as session:
        return list(
            session.execute(
                select(Account.id, Account.stripe_customer_id).where(
                    Account.trial_ends_at.is_not(None)
                )
            ).all()
        )


class _account_session:  # noqa: N801 - a context manager, named like one
    """`with _account_session(id) as (session, account):` — one tenant bound, one session open.

    A class rather than `@contextmanager` because it nests two context managers whose order
    matters: the tenant binding must be established **before** the session, or the first write
    stamps `account_id` from an unset ContextVar and raises `LookupError` (SPEC-002 D3's
    fail-closed direction, working as designed).
    """

    def __init__(self, account_id) -> None:
        self._account_id = account_id
        self._ctx = None
        self._session_cm = None

    def __enter__(self):
        from mihomes.db import get_session
        from mihomes.models.account import Account
        from mihomes.tenancy import account_context

        self._ctx = account_context(self._account_id)
        self._ctx.__enter__()
        self._session_cm = get_session()
        session = self._session_cm.__enter__()
        return session, session.get(Account, self._account_id)

    def __exit__(self, *exc) -> None:
        try:
            if self._session_cm is not None:
                self._session_cm.__exit__(*exc)
        finally:
            if self._ctx is not None:
                self._ctx.__exit__(*exc)
