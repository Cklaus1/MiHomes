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

    try:
        provider = get_billing_provider("stripe")
    except BillingProviderError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    checked = corrected = failed = 0
    for account_id, customer_id in _accounts_with_customers():
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
