"""Cron setup helper — prints recommended crontab entries."""

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()
app = typer.Typer(name="cron", help="Cron job helpers")


@app.command("setup")
def cron_setup():
    """Print recommended crontab entries for MiHomes automation."""
    console.print(Panel(
        "[bold]Recommended crontab entries:[/bold]\n\n"
        "# MiHomes daily alerts check (7am)\n"
        "0 7 * * * mihomes alerts --format brief\n\n"
        "# MiHomes weekly AI review (Monday 8am)\n"
        "0 8 * * 1 mihomes ai review --format brief\n\n"
        "# MiHomes recurring expense generation (1st of month)\n"
        "0 6 1 * * mihomes recurring generate\n\n"
        "# MiHomes weekly backup (Sunday 2am)\n"
        "0 2 * * 0 mihomes backup\n\n"
        "[dim]Add these to your crontab with: crontab -e\n"
        "Pipe output to mail/ntfy/pushover for notifications.[/dim]",
        title="MiHomes Cron Setup",
        expand=False,
    ))
