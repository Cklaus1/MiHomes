"""MiHomes CLI — root Typer application."""

import signal
import sys

import typer
from rich import print as rprint

from mihomes import __version__

# Ensure UTF-8 output on Windows (handles emoji and unicode in Rich tables).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Handle SIGPIPE gracefully — prevents data loss when output is piped through
# head/tail/etc. Without this, piping kills the process before session commits.
# SIGPIPE is not available on Windows.
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

app = typer.Typer(
    name="mihomes",
    help="MiHomes — AI-first multi-home estate management",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    account: str = typer.Option(
        None,
        "--account",
        help="Account slug to operate on. Optional when the install has exactly one account.",
    ),
):
    """MiHomes — AI-first multi-home estate management."""
    from mihomes.config import is_initialized
    from mihomes.logging_config import setup_logging

    # Wire the rotating file log (L1) so R1's logger.exception records persist.
    setup_logging()

    # Allow init and version without initialization
    if ctx.invoked_subcommand in ("init", "version", "help", None):
        return
    if not is_initialized():
        rprint("[yellow]MiHomes is not initialized. Run:[/yellow] [bold]mihomes init[/bold]")
        raise typer.Exit(1)

    # **`jobs` binds no tenant, and must not** — SPEC-004 Step 12 (D15).
    #
    # `_bind_account` refuses to proceed on a multi-account install without `--account`, which is
    # right for every other command: they operate *inside* one estate, and guessing which would be
    # a cross-tenant write. The billing sweeps operate *across* estates — reconciling one account
    # is not a partial success, it is the wrong thing — so requiring a tenant here made
    # `mihomes jobs` unreachable on exactly the installs it exists for.
    #
    # Found by running the command rather than by testing the service functions it wraps: the
    # sweep logic was green while the entrypoint could not be invoked at all.
    #
    # **`cron` is the same shape, found the same way.** `mihomes cron setup` prints crontab
    # lines and reads nothing — but it inherited the tenant gate, so on a multi-account install
    # the one command that tells an operator what to schedule exited 1. Found by SPEC-005 A15
    # invoking it, not by reading: every test of its output had constructed the panel directly.
    # **`ga-readiness` is the third instance, found the same way a third time.** It parses
    # `SAAS_PRD.md` and reads no database at all, but inherited the tenant gate — so on a
    # multi-account install the command that answers "can we launch" exited 1 with a list of
    # account slugs. Caught by A33 *invoking* it; every check of its content had called
    # `render()` directly, which is the same blind spot that hid the `cron` bug (BD7).
    if ctx.invoked_subcommand in ("jobs", "cron", "ga-readiness"):
        return

    _bind_account(ctx, account)


def _bind_account(ctx: typer.Context, slug: str | None) -> None:
    """Bind the tenant for the whole command (G13).

    Every write stamps `account_id` from this ContextVar and raises `LookupError` without it, so
    a CLI process that never binds one cannot write at all — which is exactly what launch gate
    S3 recorded.

    **`ctx.with_resource`, not a bare `.set()`.** Click closes the resource when the command's
    context ends, so the ContextVar is reset on the way out — including when the command raises.
    Setting the ContextVar and never resetting it happens to work in a process that exits
    immediately, and quietly stops working the moment anything invokes the CLI in-process, which
    is precisely what `CliRunner` does in 48 tests.
    """
    from mihomes.db import get_session
    from mihomes.tenancy import account_context
    from mihomes.tenancy.bootstrap import AccountResolutionError, resolve_account

    try:
        with get_session() as session:
            account_id = resolve_account(session, slug)
    except AccountResolutionError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1) from None

    ctx.with_resource(account_context(account_id))


@app.command("help")
def help_cmd():
    """Quick reference for common MiHomes commands."""
    from rich.panel import Panel
    rprint(Panel(
        "[bold]Getting Started[/bold]\n"
        "  mihomes init --demo          Load sample data\n"
        "  mihomes dashboard            Estate overview\n\n"
        "[bold]Daily Operations[/bold]\n"
        "  mihomes task list --overdue   See what's overdue\n"
        "  mihomes task complete <id>    Mark task done\n"
        "  mihomes issue add <title> -p <property> -s <severity>\n"
        "  mihomes issue resolve <id>    Resolve an issue\n\n"
        "[bold]Management[/bold]\n"
        "  mihomes property list         All properties\n"
        "  mihomes staff workload        Task counts per staff\n"
        "  mihomes budget report -p <property>\n"
        "  mihomes report spending -p <property>\n\n"
        "[bold]AI Advisory[/bold]\n"
        "  mihomes ai ask <question>     Ask the AI advisor\n"
        "  mihomes ai review             Proactive recommendations\n"
        "  mihomes ai prioritize         SPACE-ranked task ordering\n\n"
        "[bold]Automation[/bold]\n"
        "  mihomes auto run-all          Full automation sweep\n"
        "  mihomes auto digest           Daily summary\n"
        "  mihomes cron setup            Recommended cron jobs\n\n"
        "[bold]Data[/bold]\n"
        "  mihomes search <query>        Search everything\n"
        "  mihomes export csv <type>     Export to CSV\n"
        "  mihomes backup                Backup database\n"
        "  mihomes doctor                Integrity checks\n\n"
        "[dim]Use mihomes <command> --help for detailed options.[/dim]",
        title="MiHomes Quick Reference",
        expand=False,
    ))


@app.command("stats")
def stats_cmd():
    """Quick counts of everything in the system."""
    from mihomes.db import get_session
    from mihomes.models.asset import Asset
    from mihomes.models.audit_log import AuditLog
    from mihomes.models.contract import Contract
    from mihomes.models.document import Document
    from mihomes.models.event import Event
    from mihomes.models.insurance import InsurancePolicy
    from mihomes.models.issue import Issue, IssueStatus
    from mihomes.models.property import Property
    from mihomes.models.staff import Staff
    from mihomes.models.task import Task, TaskStatus
    from mihomes.models.vendor import Vendor
    from mihomes.models.work_order import WorkOrder, WorkOrderStatus

    with get_session() as session:
        stats = [
            ("Properties", session.query(Property).count()),
            ("Staff", session.query(Staff).count()),
            ("Vendors", session.query(Vendor).count()),
            ("Tasks (open)", session.query(Task).filter(Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED])).count()),
            ("Tasks (total)", session.query(Task).count()),
            ("Issues (open)", session.query(Issue).filter(Issue.status.notin_([IssueStatus.RESOLVED, IssueStatus.VERIFIED])).count()),
            ("Issues (total)", session.query(Issue).count()),
            ("Assets", session.query(Asset).filter(Asset.active.is_(True)).count()),
            ("Work Orders (open)", session.query(WorkOrder).filter(WorkOrder.status.notin_([WorkOrderStatus.COMPLETED, WorkOrderStatus.VERIFIED, WorkOrderStatus.CANCELLED])).count()),
            ("Events", session.query(Event).count()),
            ("Documents", session.query(Document).count()),
            ("Contracts", session.query(Contract).count()),
            ("Insurance Policies", session.query(InsurancePolicy).count()),
            ("Audit Log Entries", session.query(AuditLog).count()),
        ]
    from rich.table import Table
    table = Table(title="MiHomes Statistics", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    for label, count in stats:
        table.add_row(label, str(count))
    from mihomes.cli.formatters import console
    console.print(table)


@app.command("version")
def version_cmd():
    """Show MiHomes version and system info."""
    import sys

    from mihomes.config import DB_PATH, is_initialized

    rprint(f"[bold]MiHomes[/bold] v{__version__}")
    rprint(f"Python {sys.version.split()[0]}")
    rprint(f"Database: {DB_PATH} ({'exists' if is_initialized() else 'not initialized'})")


@app.command("ga-readiness")
def ga_readiness_cmd():
    """Where GA stands, against `SAAS_PRD.md`'s own definition of done (SPEC-005 Step 17).

    **Reports what is outstanding, and exits 1 when anything is.** A command that always exits 0
    is one an operator stops reading; the exit code is what lets "are we ready" be asked by
    something other than a person.

    It does **not** claim the blocked gates can be closed from here — three are founder
    decisions and one is a legal document. What it guarantees is that none of them is silently
    absent, which is A33's actual claim.
    """
    from mihomes.services.ga_readiness import Status, ga_gates, render

    # **`print`, not `rprint`.** The bullets are markdown copied verbatim from the PRD and two of
    # them open with `**[regression check, not new work]**` — Rich parses `[...]` as a style tag
    # and *deletes* it, so the operator saw a gate with its most important qualifier silently
    # removed. Measured: A33's "every bullet reaches the operator" assertion failed on exactly
    # those two. Escaping would work; not asking Rich to render untrusted document text is
    # simpler and cannot regress.
    print(render())

    outstanding = [g for g in ga_gates() if g.status is not Status.MET]
    if outstanding:
        raise typer.Exit(code=1)


# Register sub-apps
from mihomes.cli.alerts import app as alerts_app  # noqa: E402
from mihomes.cli.budget import budget_app, expense_app  # noqa: E402
from mihomes.cli.config import app as config_app  # noqa: E402
from mihomes.cli.issue import app as issue_app  # noqa: E402
from mihomes.cli.note import app as note_app  # noqa: E402
from mihomes.cli.property import app as property_app  # noqa: E402
from mihomes.cli.space import app as space_app  # noqa: E402
from mihomes.cli.staff import app as staff_app  # noqa: E402
from mihomes.cli.task import app as task_app  # noqa: E402
from mihomes.cli.vendor import app as vendor_app  # noqa: E402
from mihomes.cli.zone import app as zone_app  # noqa: E402

app.add_typer(property_app, name="property")
app.add_typer(space_app, name="space")
app.add_typer(zone_app, name="zone")
app.add_typer(staff_app, name="staff")
app.add_typer(vendor_app, name="vendor")
app.add_typer(task_app, name="task")
app.add_typer(issue_app, name="issue")
app.add_typer(budget_app, name="budget")
app.add_typer(expense_app, name="expense")
app.add_typer(note_app, name="note")
app.add_typer(config_app, name="config")
app.add_typer(alerts_app, name="alerts")

from mihomes.cli.audit import app as audit_app  # noqa: E402
from mihomes.cli.dashboard import app as dashboard_app  # noqa: E402
from mihomes.cli.import_cmd import app as import_app  # noqa: E402

app.add_typer(dashboard_app, name="dashboard")
app.add_typer(audit_app, name="audit")
app.add_typer(import_app, name="import")

# Phase 1b sub-apps
from mihomes.cli.backup import app as backup_app  # noqa: E402
from mihomes.cli.contract import app as contract_app  # noqa: E402
from mihomes.cli.doctor import app as doctor_app  # noqa: E402
from mihomes.cli.insurance import app as insurance_app  # noqa: E402
from mihomes.cli.recurring import app as recurring_app  # noqa: E402
from mihomes.cli.search import app as search_app  # noqa: E402
from mihomes.cli.tag import app as tag_app  # noqa: E402
from mihomes.cli.template import app as template_app  # noqa: E402

app.add_typer(contract_app, name="contract")
app.add_typer(recurring_app, name="recurring")
app.add_typer(insurance_app, name="insurance")
app.add_typer(template_app, name="template")
app.add_typer(tag_app, name="tag")
app.add_typer(search_app, name="search")
app.add_typer(backup_app, name="backup")
app.add_typer(doctor_app, name="doctor")

# Phase 2: AI sub-apps
from mihomes.cli.ai import app as ai_app  # noqa: E402
from mihomes.cli.cron import app as cron_app  # noqa: E402

# SPEC-004 Step 12 (D15) — `jobs trial-sweep` / `jobs reconcile`. Unlike every other command
# group, these sweep *across* accounts and bind tenant context per account inside the loop, so
# they must not be given a `--account` option: there is no single tenant for them to run as.
from mihomes.cli.jobs import app as jobs_app  # noqa: E402

app.add_typer(ai_app, name="ai")
app.add_typer(cron_app, name="cron")
app.add_typer(jobs_app, name="jobs")

# Phase 3a sub-apps
from mihomes.cli.asset import app as asset_app  # noqa: E402
from mihomes.cli.document import app as document_app  # noqa: E402
from mihomes.cli.event import app as event_app  # noqa: E402
from mihomes.cli.guest import app as guest_app  # noqa: E402
from mihomes.cli.report import app as report_app  # noqa: E402
from mihomes.cli.work_order import app as workorder_app  # noqa: E402

app.add_typer(asset_app, name="asset")
app.add_typer(workorder_app, name="workorder")
app.add_typer(event_app, name="event")
app.add_typer(guest_app, name="guest")
app.add_typer(document_app, name="document")
app.add_typer(report_app, name="report")

# Phase 4: Automation sub-apps
from mihomes.cli.automation import app as auto_app  # noqa: E402
from mihomes.cli.calendar_import import app as calendar_app  # noqa: E402
from mihomes.cli.inventory import app as inventory_app  # noqa: E402
from mihomes.cli.weather import app as weather_app  # noqa: E402

app.add_typer(auto_app, name="auto")
app.add_typer(calendar_app, name="calendar")
app.add_typer(inventory_app, name="inventory")
app.add_typer(weather_app, name="weather")

# Archive
from mihomes.cli.archive import app as archive_app  # noqa: E402

app.add_typer(archive_app, name="archive")

# Telegram + CSV
from mihomes.cli.csv_cmd import export_app, import_app  # noqa: E402
from mihomes.cli.telegram import app as telegram_app  # noqa: E402

app.add_typer(telegram_app, name="telegram")
app.add_typer(export_app, name="export")
app.add_typer(import_app, name="import-csv")

# Playbooks
from mihomes.cli.playbook import app as playbook_app  # noqa: E402

app.add_typer(playbook_app, name="playbook")

# Register init command directly on root app
from mihomes.cli.init import register_init  # noqa: E402

register_init(app)
