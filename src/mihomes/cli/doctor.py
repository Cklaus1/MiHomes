"""Doctor CLI command — integrity checks."""

import typer

from mihomes.cli.formatters import console
from mihomes.services.backup import run_doctor

app = typer.Typer(name="doctor", help="Run integrity checks", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def doctor(ctx: typer.Context):
    """Run integrity checks on database and media."""
    if ctx.invoked_subcommand is not None:
        return
    findings = run_doctor()
    icons = {"ok": "[green]OK[/green]", "error": "[red]ERROR[/red]", "warning": "[yellow]WARN[/yellow]", "info": "[dim]INFO[/dim]"}
    for f in findings:
        icon = icons.get(f["level"], "")
        console.print(f"  {icon}  {f['message']}")
