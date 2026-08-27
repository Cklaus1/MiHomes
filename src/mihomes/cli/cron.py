"""Cron setup helper — prints the crontab entries an operator should install.

**This is the deployment manifest A15 checks against**, and it was wrong before SPEC-005 gave it
a criterion. Its four entries were hand-written, and SPEC-004 then added `jobs reconcile` and
`jobs trial-sweep` without either reaching this list — so the two workloads that keep billing
correct were, by this file's account, not scheduled at all. Nothing failed; nothing could.

That is the exact drift A15 describes, having already happened once. So the scheduled-jobs half
now prints from `cli/jobs.py::SCHEDULE`, and `test_jobs_enumeration.py` asserts that every
command registered on the `jobs` app appears there. A seventh workload added without a cadence
fails the suite instead of silently never running.

The remaining hand-written entries are the pre-SaaS single-user helpers (`alerts`, `ai review`,
`recurring generate`, `backup`). They are not `jobs` workloads, they bind one tenant, and they
predate the multi-account model — kept because a single-user install still wants them, listed
separately because they are not what the scheduler this phase specifies is for.
"""

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()
app = typer.Typer(name="cron", help="Cron job helpers")

#: Pre-SaaS helpers. One tenant each, unlike everything in `SCHEDULE`.
LEGACY_ENTRIES: tuple[tuple[str, str, str], ...] = (
    ("0 7 * * *", "mihomes alerts --format brief", "daily alerts check"),
    ("0 8 * * 1", "mihomes ai review --format brief", "weekly AI review"),
    ("0 6 1 * *", "mihomes recurring generate", "recurring expense generation"),
    ("0 2 * * 0", "mihomes backup", "weekly backup"),
)


def _scheduled_jobs_block() -> str:
    """The `jobs` workloads, rendered from `SCHEDULE` — never from a second list here."""
    from mihomes.cli.jobs import SCHEDULE

    lines = []
    for name, (expression, reason) in SCHEDULE.items():
        lines.append(f"# {reason}")
        lines.append(f"{expression} mihomes jobs {name}")
        lines.append("")
    return "\n".join(lines).rstrip("\n")


@app.command("setup")
def cron_setup():
    """Print recommended crontab entries for MiHomes automation."""
    legacy = "\n\n".join(
        f"# MiHomes {label}\n{expression} {command}"
        for expression, command, label in LEGACY_ENTRIES
    )

    console.print(Panel(
        "[bold]Scheduled jobs — every account, required for SaaS operation:[/bold]\n\n"
        f"{_scheduled_jobs_block()}\n\n"
        "[bold]Single-tenant helpers:[/bold]\n\n"
        f"{legacy}\n\n"
        "[dim]Add these to your crontab with: crontab -e\n"
        "Every `jobs` command is idempotent, so running one twice is safe.[/dim]",
        title="MiHomes Cron Setup",
        expand=False,
    ))
