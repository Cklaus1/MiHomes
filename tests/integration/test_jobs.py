"""G12 · §6 Step 12 — the scheduled-job entrypoints (A16, A9).

**A16 is "both jobs are no-ops on a second consecutive run", and the emphasis is on *second*.**
Stripe retries, schedulers double-fire, and a Fly scheduled machine that restarts mid-run will
simply run the whole command again. A job that is correct once and wrong twice is a job that
silently corrupts state the first time infrastructure hiccups.

Tested at the **service layer** rather than through `CliRunner`, deliberately: the CLI modules
that use `cli_database` share one committed database across a module (see the root conftest), and
a sweep that writes to every account would leak into the other four modules using it. The Typer
commands are thin wrappers over the functions asserted here — `test_the_commands_are_registered`
pins that they exist and are reachable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mihomes.models.account import Account
from mihomes.services.billing.provider import SubscriptionState


class FakeProvider:
    """Returns a settable `SubscriptionState`, and counts how often it was asked."""

    def __init__(self, state: SubscriptionState) -> None:
        self.state = state
        self.calls = 0

    def get_subscription(self, *, customer_id: str) -> SubscriptionState:
        self.calls += 1
        return self.state


def _state(plan="pro", status="active", sub_id="sub_1") -> SubscriptionState:
    return SubscriptionState(
        provider_subscription_id=sub_id,
        plan=plan,
        status=status,
        current_period_end=datetime(2026, 12, 1, tzinfo=UTC),
        cancel_at_period_end=False,
    )


class TestReconcileIsIdempotent:
    def test_reconcile_is_idempotent(self, session, account_a):
        """**A16** — the second consecutive run corrects nothing.

        Idempotence here is not a property bolted on: `reconcile` re-fetches and compares, so
        "nothing changed" is what a correct second run *means*. Asserted through
        `apply_subscription_state`'s return value, which is what the sweep counts as "corrected"
        and what Step 18 will report as drift.
        """
        from mihomes.services.billing.service import apply_subscription_state

        account = session.get(Account, account_a)
        state = _state()

        assert apply_subscription_state(session, account, state) is True
        assert apply_subscription_state(session, account, state) is False, (
            "a second reconcile of unchanged remote state must correct nothing — otherwise "
            "every sweep would look like it found a problem"
        )

    def test_drift_corrected(self, session, account_a):
        """**A9** — a dropped webhook is corrected by one sweep.

        The scenario the job exists for: Stripe sent `subscription.updated`, the delivery failed,
        and nothing else would ever revisit that account. Local state says Free; the provider says
        Pro. One reconcile closes the gap.
        """
        from mihomes.services.billing.service import apply_subscription_state

        account = session.get(Account, account_a)
        account.plan = "free"
        account.subscription_status = None
        session.commit()

        changed = apply_subscription_state(session, account, _state("pro", "active"))

        assert changed is True
        assert account.plan == "pro"
        assert account.subscription_status == "active"

    def test_a_downgrade_is_also_drift(self, session, account_a):
        """Drift runs both ways, and the missed-cancellation direction is the expensive one.

        A dropped `subscription.deleted` leaves a cancelled customer on Pro entitlements
        indefinitely — the product being given away, which is harder to notice than a paying
        customer being under-served because nobody complains.
        """
        from mihomes.services.billing.service import apply_subscription_state

        account = session.get(Account, account_a)
        account.plan = "pro"
        account.subscription_status = "active"
        session.commit()

        apply_subscription_state(session, account, _state("pro", "canceled"))

        assert account.subscription_status == "canceled"


class TestTrialSweep:
    def test_expiry_reverts_to_free(self, session, account_a):
        from mihomes.cli.jobs import _expire_trial

        account = session.get(Account, account_a)
        account.plan = "pro"
        account.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        account.trial_used_at = datetime(2026, 8, 1, tzinfo=UTC)
        session.commit()

        _expire_trial(session, account)

        assert account.plan == "free"
        assert account.trial_ends_at is None

    def test_expiry_keeps_trial_used_at(self, session, account_a):
        """**A18's foundation, and the quietest way to give the product away.**

        `trial_used_at` is what enforces *"one trial per account, ever"*. Clearing it on expiry —
        which reads as tidy-up — would hand every account an unlimited supply of trials. The
        column survives expiry precisely because it is a record of history, not of state.
        """
        from mihomes.cli.jobs import _expire_trial

        account = session.get(Account, account_a)
        account.plan = "pro"
        account.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        account.trial_used_at = datetime(2026, 8, 1, tzinfo=UTC)
        session.commit()

        _expire_trial(session, account)

        assert account.trial_used_at is not None, (
            "clearing trial_used_at on expiry would give every account unlimited trials (A18)"
        )

    def test_expiry_is_idempotent(self, session, account_a):
        """**A16** for the trial half: the second run finds no trial to expire.

        `trial_ends_at` is cleared, so the account no longer appears in the sweep's query at all —
        which is why the sweep's own selection is `trial_ends_at IS NOT NULL` rather than a plan
        check. An account that expired yesterday must not be re-expired every night forever.
        """
        from mihomes.cli.jobs import _expire_trial

        account = session.get(Account, account_a)
        account.plan = "pro"
        account.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()

        _expire_trial(session, account)
        assert account.trial_ends_at is None

        # The second sweep does not see it — the query filters on trial_ends_at IS NOT NULL.
        from mihomes.cli.jobs import _accounts_with_trials

        assert account_a not in [row[0] for row in _accounts_with_trials()]

    def test_expiry_deletes_nothing(self, session, account_a):
        """`PRICING` §4.3 — *"we never delete data for a billing lapse."*

        A trial that ran four homes keeps all four: over-limit and read-only, never removed. The
        same non-destructive shape as a voluntary downgrade, and the reason expiry only touches
        two columns.
        """
        from mihomes.cli.jobs import _expire_trial
        from mihomes.services.property import create_property

        account = session.get(Account, account_a)
        account.plan = "estate"
        account.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()

        create_property(session, "Trial House")
        before = session.query(_property_model()).count()

        _expire_trial(session, account)

        assert session.query(_property_model()).count() == before, (
            "expiry must delete nothing — surplus homes go read-only (PRICING §4.3)"
        )


def _property_model():
    from mihomes.models.property import Property

    return Property


class TestTheWindowIsTheProductsNumber:
    def test_trial_ending_window_is_three_days(self):
        """`PRICING` §4.3: *"show the home-picker **~3 days before expiry**, alongside the
        `trial_ending` email, so the choice is made before access changes rather than after."*

        Pinned because the number is a product decision — the user picks which home stays active
        while they still have access to all of them — and a later reader would otherwise read it
        as an arbitrary constant to tune.
        """
        from mihomes.cli.jobs import TRIAL_ENDING_WINDOW_DAYS

        assert TRIAL_ENDING_WINDOW_DAYS == 3


class TestTheCommandsExist:
    @pytest.mark.parametrize("name", ["reconcile", "trial-sweep"])
    def test_the_commands_are_registered(self, name):
        """D15's interface half — the entrypoint must be reachable, or the scheduler has nothing
        to call regardless of how correct the sweep is."""
        from mihomes.cli.jobs import app

        registered = {c.name for c in app.registered_commands}
        assert name in registered

    def test_jobs_is_mounted_on_the_root_cli(self):
        """`mihomes jobs …` — asserted at the root, because a group that exists but is never
        mounted is exactly as useful to a cron line as one that does not exist."""
        from mihomes.cli import app as root

        assert "jobs" in {g.name for g in root.registered_groups}

    def test_the_command_actually_runs_on_a_multi_account_install(self, account_a, account_b):
        """**The bug the service-layer tests could not see.**

        Everything above passes against the sweep *functions*. The first time the command was
        actually invoked it exited 1 with *"This install has 7 accounts, so --account is
        required"* — the root callback binds a tenant before any subcommand runs, which is right
        for every group except this one. `mihomes jobs` was unreachable on precisely the installs
        it exists for.

        Two accounts in the fixture, not one, because the gate only fires when there is a choice
        to make: a single-account install would have passed while multi-account production
        failed.

        `--dry-run` so this touches no state; the sweep's behaviour is asserted at the service
        layer, and what this pins is that the entrypoint can be reached at all.
        """
        from typer.testing import CliRunner

        from mihomes.cli import app as root

        result = CliRunner().invoke(root, ["jobs", "trial-sweep", "--dry-run"])

        assert result.exit_code == 0, (
            "`mihomes jobs` must run without --account — it sweeps across accounts, so there is "
            f"no single tenant to bind. Output: {result.output}"
        )
        assert "Trial sweep" in result.output

    def test_neither_command_takes_an_account(self):
        """These sweep **across** accounts, so a `--account` option would be incoherent.

        Every other command group binds one tenant for the invocation. A sweep binds per account
        inside its loop, and offering `--account` would invite a caller to reconcile one tenant
        and believe the whole estate had been swept.
        """
        import inspect

        from mihomes.cli.jobs import app

        # `CommandInfo.callback` is the undecorated function — Typer builds Click params from its
        # signature at registration, so inspecting the signature is checking the same source
        # Typer reads, one step earlier and without constructing the Click command.
        for command in app.registered_commands:
            params = set(inspect.signature(command.callback).parameters)
            assert "account" not in params, f"{command.name} must not take --account"


# ══════════════════════════════════════════════════════════════════════════════════════════
# SPEC-005 G5 · §6 Step 5 — A17: EVERY `jobs` subcommand is a no-op on a second run
# ══════════════════════════════════════════════════════════════════════════════════════════
#
# **Module-level, and at this file's own node id**, because §8 declares A17 as
# `test_jobs.py::test_idempotent`. The class-nested `test_idempotent` above shadowed that name
# while covering only `reconcile` — so `pytest tests/integration/test_jobs.py::test_idempotent`
# did not resolve, and the one it would have run asserted a sixth of the criterion. Renamed to
# `test_reconcile_is_idempotent`, which is what it actually claims.
#
# **Driving the real CLI**, unlike the service-layer tests above. This file's docstring gives the
# reason for that choice — a sweep that writes leaks across the modules sharing `cli_database` —
# and it holds for a sweep that writes. A17 runs each command twice and compares what it
# reported, which needs the *entrypoint*: SPEC-004's own bug was that every service test passed
# while `mihomes jobs` exited 1 on any multi-account install.
#
# **Enumerated from `SCHEDULE`** (G-jobs), never listed: a hand-written list lets a seventh
# workload skip this gate silently, which is the rot A15 exists to prevent one level up.

import re  # noqa: E402

from typer.testing import CliRunner  # noqa: E402

from mihomes.cli import app as _root  # noqa: E402
from mihomes.cli.jobs import SCHEDULE  # noqa: E402
from mihomes.db import get_engine, get_session, init_db  # noqa: E402


@pytest.fixture(scope="module")
def jobs_cli(cli_database, upgrade_operator_account):
    """The dedicated CLI database, migrated, with the bootstrapped account raised.

    Module-scoped and **not autouse**: the service-layer tests above use the shared `session`
    fixture and must not be moved onto this database.
    """
    init_db()
    from mihomes.tenancy.bootstrap import ensure_default_account

    upgrade_operator_account(ensure_default_account(get_engine()))
    yield


def _invoke(name: str):
    """Run `mihomes jobs <name>` exactly as a crontab line would."""
    return CliRunner().invoke(_root, ["jobs", name])


def _counts(output: str) -> list[str]:
    """The numbers a run reported.

    Compared between runs rather than parsed into meaning: each command words its summary
    differently, and what A17 needs is that the second run said the same thing as the first.
    """
    return re.findall(r"\d+", output)


@pytest.mark.parametrize("name", sorted(SCHEDULE))
def test_idempotent(name, jobs_cli):
    """**A17** — every `jobs` subcommand is a no-op on a second consecutive run.

    Not "the second run does nothing" — `reconcile` re-fetches from the provider every time by
    design. What must hold for a cron line is that running it again **changes** nothing, which
    is what identical counts across two consecutive runs demonstrate.
    """
    first = _invoke(name)
    assert first.exit_code == 0, f"`mihomes jobs {name}` failed:\n{first.output}"

    second = _invoke(name)
    assert second.exit_code == 0, f"second run of `{name}` failed:\n{second.output}"

    assert _counts(second.output) == _counts(first.output), (
        f"`mihomes jobs {name}` is not idempotent — the second consecutive run reported "
        f"different numbers.\nfirst:  {first.output.strip()}\nsecond: {second.output.strip()}"
    )


def test_the_idempotence_gate_covers_every_registered_workload():
    """**G-jobs** — the parametrization must not drift from the Typer app.

    `test_jobs_enumeration.py` proves `SCHEDULE` equals the registered commands; asserted here
    too, so that if the two come apart the failure names *this* gate rather than surfacing as a
    workload that quietly stopped being tested.
    """
    from mihomes.cli.jobs import app as jobs_app

    assert set(SCHEDULE) == {c.name for c in jobs_app.registered_commands}


def test_multi_account_is_covered_without_polluting_the_shared_database(jobs_cli):
    """SPEC-004's entrypoint bug, re-asserted for the four new workloads.

    `mihomes jobs` was registered, mounted, and exited 1 on any install with more than one
    account, because the root callback bound a tenant before the subcommand ran. Every unit
    test passed. The gate only fires when there is a choice to make, so one account proves
    nothing.

    The second account is created and removed **inside this test**, in a `finally`. An earlier
    version created it in a module fixture and left it behind — `cli_database` is shared for
    the whole session, so `test_report_upcoming` then failed with *"This install has 2
    accounts, so --account is required"*. Passing alone, failing in the suite.
    """
    from mihomes.models.account import Account

    with get_session() as session:
        extra = Account(name="Second Estate", slug="second-estate", plan="free")
        session.add(extra)
        session.commit()
        extra_id = extra.id

    try:
        for name in sorted(SCHEDULE):
            result = _invoke(name)
            assert result.exit_code == 0, (
                f"`mihomes jobs {name}` must run without --account on a multi-account "
                f"install. Output: {result.output}"
            )
    finally:
        with get_session() as session:
            session.query(Account).filter(Account.id == extra_id).delete()
            session.commit()
