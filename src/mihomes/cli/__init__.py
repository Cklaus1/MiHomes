"""MiHomes CLI — root Typer application."""

import typer
from rich import print as rprint

from mihomes import __version__

app = typer.Typer(
    name="mihomes",
    help="MiHomes — AI-first multi-home estate management",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """MiHomes — AI-first multi-home estate management."""
    from mihomes.config import is_initialized

    # Allow init and version without initialization
    if ctx.invoked_subcommand in ("init", "version", None):
        return
    if not is_initialized():
        rprint("[yellow]MiHomes is not initialized. Run:[/yellow] [bold]mihomes init[/bold]")
        raise typer.Exit(1)


@app.command("version")
def version_cmd():
    """Show MiHomes version and system info."""
    import sys
    from mihomes.config import DB_PATH, is_initialized

    rprint(f"[bold]MiHomes[/bold] v{__version__}")
    rprint(f"Python {sys.version.split()[0]}")
    rprint(f"Database: {DB_PATH} ({'exists' if is_initialized() else 'not initialized'})")


# Register sub-apps
from mihomes.cli.property import app as property_app  # noqa: E402
from mihomes.cli.space import app as space_app  # noqa: E402
from mihomes.cli.staff import app as staff_app  # noqa: E402
from mihomes.cli.vendor import app as vendor_app  # noqa: E402
from mihomes.cli.task import app as task_app  # noqa: E402
from mihomes.cli.issue import app as issue_app  # noqa: E402
from mihomes.cli.budget import budget_app, expense_app  # noqa: E402
from mihomes.cli.note import app as note_app  # noqa: E402
from mihomes.cli.config import app as config_app  # noqa: E402
from mihomes.cli.alerts import app as alerts_app  # noqa: E402

app.add_typer(property_app, name="property")
app.add_typer(space_app, name="space")
app.add_typer(staff_app, name="staff")
app.add_typer(vendor_app, name="vendor")
app.add_typer(task_app, name="task")
app.add_typer(issue_app, name="issue")
app.add_typer(budget_app, name="budget")
app.add_typer(expense_app, name="expense")
app.add_typer(note_app, name="note")
app.add_typer(config_app, name="config")
app.add_typer(alerts_app, name="alerts")

from mihomes.cli.dashboard import app as dashboard_app  # noqa: E402
from mihomes.cli.audit import app as audit_app  # noqa: E402

app.add_typer(dashboard_app, name="dashboard")
app.add_typer(audit_app, name="audit")

# Phase 1b sub-apps
from mihomes.cli.contract import app as contract_app  # noqa: E402
from mihomes.cli.recurring import app as recurring_app  # noqa: E402
from mihomes.cli.insurance import app as insurance_app  # noqa: E402
from mihomes.cli.template import app as template_app  # noqa: E402
from mihomes.cli.tag import app as tag_app  # noqa: E402
from mihomes.cli.search import app as search_app  # noqa: E402
from mihomes.cli.backup import app as backup_app  # noqa: E402
from mihomes.cli.doctor import app as doctor_app  # noqa: E402

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

app.add_typer(ai_app, name="ai")
app.add_typer(cron_app, name="cron")

# Phase 3a sub-apps
from mihomes.cli.asset import app as asset_app  # noqa: E402
from mihomes.cli.work_order import app as workorder_app  # noqa: E402
from mihomes.cli.event import app as event_app  # noqa: E402
from mihomes.cli.guest import app as guest_app  # noqa: E402
from mihomes.cli.document import app as document_app  # noqa: E402
from mihomes.cli.report import app as report_app  # noqa: E402

app.add_typer(asset_app, name="asset")
app.add_typer(workorder_app, name="workorder")
app.add_typer(event_app, name="event")
app.add_typer(guest_app, name="guest")
app.add_typer(document_app, name="document")
app.add_typer(report_app, name="report")

# Phase 4: Automation sub-apps
from mihomes.cli.automation import app as auto_app  # noqa: E402
from mihomes.cli.calendar_import import app as calendar_app  # noqa: E402

app.add_typer(auto_app, name="auto")
app.add_typer(calendar_app, name="calendar")

# WhatsApp + CSV
from mihomes.cli.whatsapp import app as whatsapp_app  # noqa: E402
from mihomes.cli.csv_cmd import export_app, import_app  # noqa: E402

app.add_typer(whatsapp_app, name="whatsapp")
app.add_typer(export_app, name="export")
app.add_typer(import_app, name="import-csv")

# Register init command directly on root app
from mihomes.cli.init import register_init  # noqa: E402
register_init(app)
