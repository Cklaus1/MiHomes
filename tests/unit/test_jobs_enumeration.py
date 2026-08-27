"""G5 · §6 Step 5 — THE enforcement test (A15). Static, tree-walking, no database.

A15 is the phase's definition of done, and the spec says why at length:

    > **A scheduler that never fires is indistinguishable from a system with nothing to do.**
    > The trial sweep, the reconciliation sweep, the outbox drain, the dunning ladder, the drips
    > and the weekly digest are six workloads with one trigger. If that trigger is misconfigured
    > — or if SPEC-004 D15's unverified assumption about Fly's scheduled machines is simply wrong
    > — every one of them stops, no exception is raised, no test fails, and the first signal is a
    > customer asking why they were never told their card failed.

So this test does not ask "does `drain-outbox` work when called" — that passes trivially. It
**enumerates the workloads from the Typer app** and asserts each is reachable and scheduled, so
it fails when someone adds a seventh without wiring it.

**The drift it is written against already happened.** Before this test existed, `mihomes cron
setup` — the only place the repo tells an operator what to schedule — listed four commands, none
of which was `reconcile` or `trial-sweep`. SPEC-004 added both. Neither reached the manifest, and
nothing anywhere failed. A hand-maintained list rots, and it rots silently.
"""

from __future__ import annotations

import inspect

import pytest

from mihomes.cli.jobs import SCHEDULE
from mihomes.cli.jobs import app as jobs_app

#: What §8's A15 means by "every scheduled workload" — the six the spec names.
#:
#: Written out so that *removing* a workload fails too. Enumeration alone cannot catch a
#: deletion: a `jobs` app with one command would satisfy "every registered command is
#: scheduled" perfectly.
EXPECTED_WORKLOADS = {
    "drain-outbox",
    "dunning",
    "drips",
    "weekly-digest",
    "trial-sweep",
    "reconcile",
}


def _registered() -> set[str]:
    """Workload names, walked from the Typer app rather than listed."""
    return {c.name for c in jobs_app.registered_commands}


def test_all_workloads_scheduled():
    """**A15** — every registered workload has a cadence, and every cadence a workload.

    Set equality in both directions, which is the point:

    * a command with no `SCHEDULE` entry is a workload nobody will ever run — the failure the
      spec describes, where nothing raises and the first signal is a customer complaint;
    * a `SCHEDULE` entry with no command is a crontab line that will fail nightly with
      "no such command", which an operator discovers from mail they have probably filtered.
    """
    registered = _registered()

    unscheduled = registered - set(SCHEDULE)
    assert not unscheduled, (
        f"these `jobs` workloads have no cadence in SCHEDULE and would never run: "
        f"{sorted(unscheduled)}. Add them to `cli/jobs.py::SCHEDULE` with a reason — "
        f"`mihomes cron setup` prints from it."
    )

    phantom = set(SCHEDULE) - registered
    assert not phantom, (
        f"SCHEDULE names workloads that are not registered commands: {sorted(phantom)}. "
        f"A crontab line for a command that does not exist fails silently into cron's mail."
    )


def test_the_six_workloads_the_spec_names_all_exist():
    """The other direction — enumeration cannot catch a deletion.

    A `jobs` app with a single command would pass `test_all_workloads_scheduled` unchanged,
    while five of the six things this phase exists to run had quietly stopped.
    """
    missing = EXPECTED_WORKLOADS - _registered()
    assert not missing, f"workloads named by SPEC-005 §8 A15 are missing: {sorted(missing)}"


def test_every_workload_is_reachable_through_the_root_cli():
    """Registered on the group is not enough — a cron line invokes `mihomes jobs <name>`.

    SPEC-004 shipped a `jobs` group that was registered, mounted, and **exited 1** on any
    multi-account install, because the root callback bound a tenant before the subcommand ran.
    Every unit test passed. This asserts the mount, and `test_jobs.py` invokes the commands.
    """
    from mihomes.cli import app as root

    assert "jobs" in {g.name for g in root.registered_groups}


def test_the_manifest_prints_every_workload():
    """`mihomes cron setup` is the deployment manifest, and it must be derived.

    Rendered from `SCHEDULE` rather than hand-written, because the hand-written version was
    already wrong: it omitted both of SPEC-004's workloads. This asserts the derivation
    survives — a future edit that inlines the entries again fails here.
    """
    from typer.testing import CliRunner

    from mihomes.cli import app as root

    result = CliRunner().invoke(root, ["cron", "setup"])
    assert result.exit_code == 0, result.output

    # Rich wraps panel content, so assert on the command names rather than whole lines.
    for name in _registered():
        assert f"jobs {name}" in result.output.replace("\n", " "), (
            f"`mihomes cron setup` does not mention `jobs {name}` — an operator reading it "
            f"would not schedule that workload"
        )


@pytest.mark.parametrize("name", sorted(EXPECTED_WORKLOADS))
def test_every_cadence_carries_a_reason(name):
    """A cron expression with no reason is a number nobody can safely change.

    `*/5` versus `0 3` is a real decision — how quickly mail must leave, versus how expensive
    the sweep is — and the next person to tune it needs to know which they are trading.
    """
    expression, reason = SCHEDULE[name]
    assert expression.count(" ") == 4, f"{name}: {expression!r} is not a 5-field cron expression"
    assert len(reason) > 40, f"{name}: the cadence needs a reason, not a label"


def test_no_workload_takes_an_account():
    """These sweep **across** accounts (SPEC-004 D15), so `--account` would be incoherent.

    Extended from `test_jobs.py`'s version to cover the four new commands: offering `--account`
    would invite a caller to drain one tenant's outbox and believe the estate's mail had gone.
    """
    for command in jobs_app.registered_commands:
        params = set(inspect.signature(command.callback).parameters)
        assert "account" not in params, f"{command.name} must not take --account"
